"""OpenAI-compatible LLM client with a process-wide USD budget cap.

Configuration for the endpoint comes only from ``LLM_BASE_URL``, ``LLM_API_KEY``
and ``LLM_MODEL``. The spend cap defaults to $2.00 and may be overridden with
``LLM_BUDGET_USD``. Every network call goes through :meth:`LLMClient._call`.
All error messages are built from fixed strings, so neither the key nor a raw
provider payload can reach a log, a response or an exception text. All money
values are ``Decimal``.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import threading
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:  # pragma: no cover - resurrection.py imports this module
    from .resurrection import ArchitectOutput

TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 64 * 1024
DEFAULT_BUDGET_USD = Decimal("2.00")
BUDGET_CAP_USD = DEFAULT_BUDGET_USD  # default cap; see budget_cap_from_env()

# Budget-model pricing (USD per token), used for cost accounting only.
_PRICE_IN = Decimal("0.10") / Decimal(1_000_000)
_PRICE_OUT = Decimal("0.50") / Decimal(1_000_000)
_ESTIMATED_OUTPUT_TOKENS = 16_384

_ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"
# Used only when .env.example is not shipped (e.g. inside the Docker image).
_FALLBACK_PLACEHOLDERS = frozenset({"your_api_key_here", "changeme", "your-api-key"})

IO_PROTOCOLS = frozenset(
    {"batch_space", "single_run_lines", "single_run_space", "no_input_static"}
)
OUTPUT_FORMATS = frozenset({"currency", "percent", "integer", "decimal", "flag", "text"})
CHART_TYPES = frozenset({"bar_comparison", "breakdown", "waterfall", "summary"})


def _load_placeholder_keys() -> frozenset[str]:
    """Read placeholder API key values from ``.env.example`` once (never ``.env``)."""
    found: set[str] = set()
    try:
        for line in _ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() == "LLM_API_KEY" and value.strip():
                found.add(value.strip().strip("'\"").lower())
    except OSError:
        pass
    return frozenset(found) | _FALLBACK_PLACEHOLDERS


_PLACEHOLDER_KEYS = _load_placeholder_keys()


def budget_cap_from_env() -> Decimal:
    """Return ``LLM_BUDGET_USD`` as Decimal; fall back to $2.00 if unset or invalid."""
    raw = (os.environ.get("LLM_BUDGET_USD") or "").strip()
    if not raw:
        return DEFAULT_BUDGET_USD
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        return DEFAULT_BUDGET_USD
    if not value.is_finite() or value < 0:
        return DEFAULT_BUDGET_USD
    return value


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str = dataclasses.field(repr=False)
    model: str


UnavailableCause = Literal["missing_key", "provider_failure", "budget_exhausted"]

_UNAVAILABLE_MESSAGES: dict[str, str] = {
    "missing_key": "LLM_API_KEY is not configured",
    "provider_failure": "LLM provider request failed or timed out",
    "budget_exhausted": "LLM budget exhausted",
}


class LLMUnavailable(Exception):
    """The LLM cannot be used. ``cause`` is one of the three fixed values."""

    def __init__(self, cause: UnavailableCause):
        super().__init__(_UNAVAILABLE_MESSAGES[cause])
        self.cause: UnavailableCause = cause


class LLMKeyMissing(LLMUnavailable):
    """LLM_API_KEY is unset, empty, or a known .env.example placeholder."""

    def __init__(self, *_: object):
        super().__init__("missing_key")


class BudgetExceeded(LLMUnavailable):
    """Cumulative LLM cost would reach the cap."""

    def __init__(self, *_: object):
        super().__init__("budget_exhausted")


class LLMCallError(Exception):
    """Unparseable or schema-violating response. Never contains the key or payload."""

    def __init__(self, stage: str, message: str):
        super().__init__(f"{stage}: {message}")
        self.stage = stage
        self.message = message


class BudgetTracker:
    def __init__(self, cap: Decimal | None = None):
        self.cap = Decimal(cap) if cap is not None else budget_cap_from_env()
        self.cumulative_cost_usd = Decimal("0")
        self._lock = threading.Lock()

    def can_spend(self, estimate: Decimal) -> bool:
        with self._lock:
            return self.cumulative_cost_usd + Decimal(estimate) < self.cap

    def record(self, actual: Decimal) -> None:
        with self._lock:
            self.cumulative_cost_usd += Decimal(actual)


_BUDGET = BudgetTracker()


def get_budget_tracker() -> BudgetTracker:
    return _BUDGET


# ------------------------------------------------------------------ parsing
_FENCE_OPEN = re.compile(r"^\s*```[A-Za-z0-9_-]*[ \t]*\r?\n?")
_FENCE_CLOSE = re.compile(r"\r?\n?[ \t]*```\s*$")


def _extract_json(text: str) -> Any:
    """Strip a leading ```json / ``` fence and a trailing fence, then parse.

    Floats become ``Decimal`` so no binary float ever holds a monetary value.
    Raises ``ValueError`` (or ``RecursionError``) on invalid JSON.
    """
    body = text.strip()
    if body.startswith("```"):
        body = _FENCE_OPEN.sub("", body, count=1)
        body = _FENCE_CLOSE.sub("", body, count=1)
    return json.loads(body.strip(), parse_float=Decimal)


# ------------------------------------------------------------ inferred model
@dataclass(frozen=True)
class InferredProgram:
    program_title: str
    description: str
    io_protocol: str
    contract: dict[str, list[dict[str, Any]]]
    business_rules: list[str]
    dashboard_metadata: dict[str, Any] = field(default_factory=dict)


class _SchemaError(ValueError):
    """Internal: schema violation. The text is fed back to the LLM on retry."""


def _req_str(d: dict[str, Any], key: str, where: str) -> str:
    v = d.get(key)
    if not isinstance(v, str) or not v.strip():
        raise _SchemaError(f"{where}.{key} must be a non-empty string")
    return v


def _opt_decimal(d: dict[str, Any], key: str, where: str) -> str | None:
    """Return a Decimal-normalized string (kept as str for JSON safety) or None."""
    v = d.get(key)
    if v is None:
        return None
    if isinstance(v, bool):
        raise _SchemaError(f"{where}.{key} must be a number")
    try:
        dec = Decimal(str(v))
    except (InvalidOperation, ValueError):
        raise _SchemaError(f"{where}.{key} must be a number") from None
    if not dec.is_finite():
        raise _SchemaError(f"{where}.{key} must be a finite number")
    return format(dec, "f")


def _scale(d: dict[str, Any], where: str) -> int:
    v = d.get("scale")
    if isinstance(v, bool) or not isinstance(v, (int, Decimal, str)):
        raise _SchemaError(f"{where}.scale must be an integer")
    try:
        dec = Decimal(str(v))
    except (InvalidOperation, ValueError):
        raise _SchemaError(f"{where}.scale must be an integer") from None
    if not dec.is_finite() or dec != dec.to_integral_value() or not 0 <= dec <= 18:
        raise _SchemaError(f"{where}.scale must be an integer between 0 and 18")
    return int(dec)


def parse_inferred_program(data: Any) -> InferredProgram:
    """Validate an LLM reply against the InferredProgram schema (Req 5.2 to 5.7)."""
    if not isinstance(data, dict):
        raise _SchemaError("reply must be a JSON object")
    protocol = _req_str(data, "io_protocol", "reply")
    if protocol not in IO_PROTOCOLS:
        raise _SchemaError(f"io_protocol must be one of {sorted(IO_PROTOCOLS)}")
    contract = data.get("contract")
    if not isinstance(contract, dict):
        raise _SchemaError("contract must be an object with inputs and outputs")
    raw_in, raw_out = contract.get("inputs"), contract.get("outputs")
    if not isinstance(raw_in, list) or not isinstance(raw_out, list):
        raise _SchemaError("contract.inputs and contract.outputs must be lists")

    inputs: list[dict[str, Any]] = []
    for i, f in enumerate(raw_in):
        where = f"contract.inputs[{i}]"
        if not isinstance(f, dict):
            raise _SchemaError(f"{where} must be an object")
        item: dict[str, Any] = {
            "name": _req_str(f, "name", where),
            "label": _req_str(f, "label", where),
            "pic": _req_str(f, "pic", where),
            "scale": _scale(f, where),
            "min": _opt_decimal(f, "min", where),
            "max": _opt_decimal(f, "max", where),
            "step": _opt_decimal(f, "step", where),
        }
        default = _opt_decimal(f, "default", where)
        if default is not None:
            item["default"] = default
        inputs.append(item)
    outputs: list[dict[str, Any]] = []
    for i, f in enumerate(raw_out):
        where = f"contract.outputs[{i}]"
        if not isinstance(f, dict):
            raise _SchemaError(f"{where} must be an object")
        fmt = _req_str(f, "format", where)
        if fmt not in OUTPUT_FORMATS:
            raise _SchemaError(f"{where}.format must be one of {sorted(OUTPUT_FORMATS)}")
        outputs.append(
            {
                "name": _req_str(f, "name", where),
                "label": _req_str(f, "label", where),
                "pic": _req_str(f, "pic", where),
                "scale": _scale(f, where),
                "rounding": _req_str(f, "rounding", where),
                "format": fmt,
            }
        )

    rules = data.get("business_rules")
    if not isinstance(rules, list) or not all(isinstance(r, str) for r in rules):
        raise _SchemaError("business_rules must be a list of strings")
    meta = data.get("dashboard_metadata")
    if not isinstance(meta, dict):
        raise _SchemaError("dashboard_metadata must be an object")
    if meta.get("chart_type") not in CHART_TYPES:
        raise _SchemaError(f"dashboard_metadata.chart_type must be one of {sorted(CHART_TYPES)}")
    if not isinstance(meta.get("title"), str) or not meta["title"].strip():
        raise _SchemaError("dashboard_metadata.title must be a non-empty string")
    if not isinstance(meta.get("inputs"), list) or not isinstance(meta.get("outputs"), list):
        raise _SchemaError("dashboard_metadata.inputs and .outputs must be lists")

    return InferredProgram(
        program_title=_req_str(data, "program_title", "reply"),
        description=_req_str(data, "description", "reply"),
        io_protocol=protocol,
        contract={"inputs": inputs, "outputs": outputs},
        business_rules=list(rules),
        dashboard_metadata=meta,
    )


def _to_jsonable(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    return obj


def _dumps(obj: Any) -> str:
    return json.dumps(_to_jsonable(obj), default=str, sort_keys=True)


_SYSTEM = (
    "You are a COBOL modernization engine for banking code. Reply with a single "
    "JSON object only. Never use floats for money; use Decimal."
)

_INFER_PROMPT = (
    "Analyze this COBOL program and infer its contract. Return one JSON object: "
    '{"program_title": str, "description": str, "io_protocol": one of '
    '"batch_space" | "single_run_lines" | "single_run_space" | "no_input_static", '
    '"contract": {"inputs": [{"name": str, "label": str, "pic": str, "scale": int, '
    '"min": number, "max": number, "step": number}], "outputs": [{"name": str, '
    '"label": str, "pic": str, "scale": int, "rounding": "ROUND_DOWN" | '
    '"ROUND_HALF_UP", "format": "currency" | "percent" | "integer" | "decimal" | '
    '"flag" | "text"}]}, "business_rules": [plain-English str], '
    '"dashboard_metadata": {"title": str, "chart_type": "bar_comparison" | '
    '"breakdown" | "waterfall" | "summary", "inputs": [..], "outputs": [..]}}. '
    "Use no_input_static only when the program reads no input. "
    "For every input choose a realistic, non-overflowing operational min and max "
    "that sit safely inside the field's PIC capacity so intermediate COBOL sums "
    "and products never silently wrap around (for PIC S9(9)V99 balances use "
    'min "-1000000.00", max "1000000.00", step "10.00", not -999999999.99). '
    'Also give every input a realistic "default" decimal string inside [min, max] '
    '(for example "1000.00", "5.000", "12"). min, max, step and default are '
    "decimal strings with the field's scale."
)

_SANDBOX_RULES = (
    "SANDBOX RULES (the code runs in a restricted interpreter):\n"
    "1. NO import statements at all, not even `from decimal import Decimal`. "
    "`Decimal`, `ROUND_HALF_UP` and `ROUND_DOWN` are already defined as globals. "
    "Never write `decimal.Decimal` or `decimal.Context(...)`; call `Decimal(...)` "
    'and `.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)` (or ROUND_DOWN) directly.\n'
    "2. SCALAR-IN, SCALAR-OUT ONLY. Whatever the io_protocol (batch_space, "
    "single_run_lines, single_run_space, no_input_static), `def calculate(**inputs) "
    "-> dict:` is called once per single test case with scalar inputs (Decimal for "
    "decimal fields, int for scale-0 integer fields, already validated and finite). "
    "It MUST return a flat dict mapping each output field name to a single scalar "
    "Decimal or int. Never lists, never batch arrays, never dicts of lists.\n"
    "3. Allowed builtins ONLY: abs, min, max, int, len, range, sum, divmod, round, "
    "bool. Do not use isinstance, str, zip, enumerate, any, all, ValueError, "
    "KeyError, classes or generator expressions. Write plain `for` loops and "
    "if/else statements.\n"
    "4. Match COBOL exactly: ROUNDED means ROUND_HALF_UP, no ROUNDED means "
    "truncation (ROUND_DOWN). Quantize only where COBOL stores into a field and "
    "keep intermediate multiplications and divisions exact in Decimal.\n"
)

_SANDBOX_EXAMPLE = (
    "Example of the expected style:\n"
    "def calculate(**inputs) -> dict:\n"
    '    p = inputs["principal"]\n'
    '    r = inputs["rate"] / Decimal(1200)\n'
    '    fee = (p * r).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)\n'
    '    total = (p + fee).quantize(Decimal("0.01"), rounding=ROUND_DOWN)\n'
    '    return {"fee": fee, "total": total}\n'
)

_NO_FLOAT_RULE = (
    "5. NEVER write the identifier `float` anywhere (not in comments or type "
    "checks) and never write float literals such as 0.0 or 100.0. Use "
    'Decimal("0.00") and Decimal(100).\n'
)

_FLOAT_DEMO_RULE = (
    "5. DEMONSTRATION: do the arithmetic directly with Python float operations "
    "(float(...), /, **, round(..., 2) or int(x * 100) / 100) so real IEEE-754 "
    "drift shows against GnuCOBOL. Still no imports, no generator expressions, no "
    "isinstance or ValueError, and scalar-in, scalar-out only.\n"
)

_DECIMAL_IMPORT = re.compile(
    r"^[ \t]*(?:import[ \t]+decimal(?:[ \t]+as[ \t]+\w+)?|from[ \t]+decimal[ \t]+import[ \t]+[^\n]*)[ \t]*(?:#[^\n]*)?$\r?\n?",
    re.MULTILINE,
)
_DECIMAL_ATTR = re.compile(r"\bdecimal\.(Decimal|ROUND_HALF_UP|ROUND_DOWN)\b")


def _clean_generated_code(code: str) -> str:
    """Strip markdown fences, decimal-only imports and ``decimal.`` prefixes.

    Other imports are left alone so the AST sandbox still rejects them.
    """
    body = code.strip()
    if body.startswith("```"):
        body = _FENCE_OPEN.sub("", body, count=1)
        body = _FENCE_CLOSE.sub("", body, count=1)
    body = _DECIMAL_IMPORT.sub("", body)
    body = _DECIMAL_ATTR.sub(r"\1", body)
    return body.strip() + "\n"


class LLMClient:
    def __init__(self, config: LLMConfig | None, budget_tracker: BudgetTracker):
        self.config = config
        self.budget_tracker = budget_tracker

    @classmethod
    def from_env(cls, tracker: BudgetTracker | None = None) -> "LLMClient":
        tracker = tracker if tracker is not None else _BUDGET
        key = (os.environ.get("LLM_API_KEY") or "").strip()
        base_url = (os.environ.get("LLM_BASE_URL") or "").strip()
        model = (os.environ.get("LLM_MODEL") or "").strip()
        if not key or key.lower() in _PLACEHOLDER_KEYS or not model:
            return cls(None, tracker)
        return cls(LLMConfig(base_url=base_url, api_key=key, model=model), tracker)

    def require_key(self) -> None:
        if self.config is None:
            raise LLMUnavailable("missing_key")

    # ------------------------------------------------------------------ stages
    def infer_contract(
        self, cobol_source: str, validation_error: str | None = None
    ) -> InferredProgram:
        """Stage 1. One retry carrying the validation error, then ``LLMCallError``."""
        stage = "legacy_parser"
        error = validation_error
        for attempt in (1, 2):
            prompt = _INFER_PROMPT
            if error:
                prompt += (
                    "\n\nYour previous reply was rejected for this reason; fix it: "
                    f"{error[:500]}"
                )
            prompt += f"\n\nCOBOL:\n{cobol_source}"
            try:
                return parse_inferred_program(self._call(stage, prompt))
            except _SchemaError as exc:
                error = str(exc)
            except LLMCallError as exc:
                error = exc.message
            if attempt == 2:
                raise LLMCallError(stage, f"response did not match the schema: {error}")
        raise AssertionError("unreachable")  # pragma: no cover

    def generate_python(
        self, cobol_source: str, program: InferredProgram, *, float_demo: bool = False
    ) -> "ArchitectOutput":
        style = _SANDBOX_RULES + (_FLOAT_DEMO_RULE if float_demo else _NO_FLOAT_RULE)
        prompt = (
            "Write Python 3.12 source defining `def calculate(**inputs) -> dict` "
            "that reproduces this COBOL program exactly.\n"
            + style
            + _SANDBOX_EXAMPLE
            + 'Return JSON: {"verified_python_code": str, "dashboard_metadata": object}.\n\n'
            f"Contract: {_dumps(program.contract)}\n"
            f"Protocol: {program.io_protocol}\n"
            f"Rules: {_dumps(program.business_rules)}\n\nCOBOL:\n{cobol_source}"
        )
        return self._architect("cloud_architect", prompt)

    def repair_python(
        self,
        cobol_source: str,
        program: InferredProgram,
        counterexample: Any,
        previous_code: str,
        *,
        float_demo: bool = False,
    ) -> "ArchitectOutput":
        style = _SANDBOX_RULES + (_FLOAT_DEMO_RULE if float_demo else _NO_FLOAT_RULE)
        prompt = (
            "The Python below disagrees with the real COBOL on the counterexample. "
            "Fix it so it matches the COBOL exactly.\n"
            + style
            + _SANDBOX_EXAMPLE
            + 'Return JSON: {"verified_python_code": str, "dashboard_metadata": object}.\n\n'
            f"Counterexample: {_dumps(counterexample)}\n"
            f"Contract: {_dumps(program.contract)}\n"
            f"Protocol: {program.io_protocol}\n"
            f"Rules: {_dumps(program.business_rules)}\n\n"
            f"Previous code:\n{previous_code}\n\nCOBOL:\n{cobol_source}"
        )
        return self._architect("parity_auditor", prompt)

    def _architect(self, stage: str, prompt: str) -> "ArchitectOutput":
        from .resurrection import ArchitectOutput

        data = self._call(stage, prompt)
        code = data.get("verified_python_code") if isinstance(data, dict) else None
        meta = data.get("dashboard_metadata") if isinstance(data, dict) else None
        if not isinstance(code, str) or not code.strip() or not isinstance(meta, dict):
            raise LLMCallError(stage, "response did not match the expected schema")
        return ArchitectOutput(
            verified_python_code=_clean_generated_code(code), dashboard_metadata=meta
        )

    # ------------------------------------------------------------------- _call
    def _call(self, stage: str, prompt: str) -> Any:
        """One request: key check, budget check, 30 s timeout, 64 KiB cap."""
        self.require_key()
        assert self.config is not None
        estimate = (
            Decimal(len(prompt) // 4 + 1) * _PRICE_IN
            + Decimal(_ESTIMATED_OUTPUT_TOKENS) * _PRICE_OUT
        )
        if not self.budget_tracker.can_spend(estimate):
            raise LLMUnavailable("budget_exhausted")

        try:
            from openai import OpenAI

            client = OpenAI(
                base_url=self.config.base_url or None,
                api_key=self.config.api_key,
                timeout=TIMEOUT_SECONDS,
                max_retries=0,
            )
            request: dict[str, Any] = {
                "model": self.config.model,
                "messages": [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
            }
            try:
                resp = client.chat.completions.create(reasoning_effort="low", **request)
            except Exception:  # noqa: BLE001 - endpoint may not accept reasoning_effort
                resp = client.chat.completions.create(**request)
        except Exception:  # noqa: BLE001 - normalized; raw error may hold secrets
            raise LLMUnavailable("provider_failure") from None

        try:
            usage = getattr(resp, "usage", None)
            tokens_in = int(getattr(usage, "prompt_tokens", 0) or 0)
            tokens_out = int(getattr(usage, "completion_tokens", 0) or 0)
            actual = Decimal(tokens_in) * _PRICE_IN + Decimal(tokens_out) * _PRICE_OUT
            self.budget_tracker.record(actual if usage is not None else estimate)
            text = resp.choices[0].message.content
        except Exception:  # noqa: BLE001
            self.budget_tracker.record(estimate)
            raise LLMCallError(stage, "LLM response was malformed") from None

        if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise LLMCallError(stage, "LLM response was empty or exceeded the size cap")
        try:
            return _extract_json(text)
        except (ValueError, RecursionError):
            raise LLMCallError(stage, "LLM response was not valid JSON") from None
