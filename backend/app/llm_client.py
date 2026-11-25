"""OpenAI-compatible LLM client with a process-wide $2.00 budget cap.

Configuration comes only from ``LLM_BASE_URL``, ``LLM_API_KEY`` and ``LLM_MODEL``.
Every network call goes through :meth:`LLMClient._call`, which normalizes every
failure to :class:`LLMCallError` without leaking the key or the raw provider
payload. All money values are ``Decimal``.
"""

from __future__ import annotations

import dataclasses
import json
import os
import threading
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - resurrection.py imports this module
    from .resurrection import ArchitectOutput, ContractSchema, ParsedRules

TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 64 * 1024
BUDGET_CAP_USD = Decimal("2.00")

# Budget-model pricing (USD per token), used for cost accounting only.
_PRICE_IN = Decimal("0.05") / Decimal(1_000_000)
_PRICE_OUT = Decimal("0.40") / Decimal(1_000_000)
_ESTIMATED_OUTPUT_TOKENS = 16_384

_ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"
# Used only when .env.example is not shipped (e.g. inside the Docker image).
_FALLBACK_PLACEHOLDERS = frozenset({"your_api_key_here", "changeme", "your-api-key"})


def _load_placeholder_keys() -> frozenset[str]:
    """Read placeholder API key values from ``.env.example`` once."""
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


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str = dataclasses.field(repr=False)
    model: str


class LLMKeyMissing(Exception):
    """LLM_API_KEY is unset, empty, or a known .env.example placeholder."""


class LLMCallError(Exception):
    """Timeout or unparseable response. Never contains the key or raw payload."""

    def __init__(self, stage: str, message: str):
        super().__init__(f"{stage}: {message}")
        self.stage = stage
        self.message = message


class BudgetExceeded(Exception):
    """Cumulative LLM cost would reach the $2.00 cap."""


class BudgetTracker:
    def __init__(self, cap: Decimal = BUDGET_CAP_USD):
        self.cap = cap
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


def _to_jsonable(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    return obj


def _dumps(obj: Any) -> str:
    return json.dumps(_to_jsonable(obj), default=str, sort_keys=True)


_SYSTEM = (
    "You are a COBOL modernization engine for banking code. Reply with a single "
    "JSON object only. Never use floats for money; use decimal.Decimal."
)


class LLMClient:
    def __init__(self, config: LLMConfig | None, budget_tracker: BudgetTracker):
        self.config = config
        self.budget_tracker = budget_tracker

    @classmethod
    def from_env(cls, budget_tracker: BudgetTracker | None = None) -> "LLMClient":
        tracker = budget_tracker if budget_tracker is not None else _BUDGET
        key = (os.environ.get("LLM_API_KEY") or "").strip()
        base_url = (os.environ.get("LLM_BASE_URL") or "").strip()
        model = (os.environ.get("LLM_MODEL") or "").strip()
        if not key or key.lower() in _PLACEHOLDER_KEYS or not model:
            return cls(None, tracker)
        return cls(LLMConfig(base_url=base_url, api_key=key, model=model), tracker)

    def require_key(self) -> None:
        if self.config is None:
            raise LLMKeyMissing("LLM_API_KEY is not configured")

    # ------------------------------------------------------------------ stages
    def extract_rules(self, cobol_source: str) -> "ParsedRules":
        from .resurrection import ParsedRules

        stage = "legacy_parser"
        prompt = (
            "Extract plain-English business rules and PIC mappings from this COBOL "
            'program. Return JSON: {"business_rules": [str], "pic_mappings": '
            '[{"name": str, "pic": str, "scale": str, "rounding": str}]}.\n\n'
            f"COBOL:\n{cobol_source}"
        )
        data = self._call(stage, prompt)
        try:
            rules = data["business_rules"]
            maps = data["pic_mappings"]
            if not isinstance(rules, list) or not isinstance(maps, list):
                raise TypeError
            return ParsedRules(
                business_rules=[str(r) for r in rules],
                pic_mappings=[{str(k): str(v) for k, v in m.items()} for m in maps],
            )
        except (KeyError, TypeError, AttributeError):
            raise LLMCallError(stage, "response did not match the expected schema") from None

    def generate_python(
        self, cobol_source: str, rules: "ParsedRules", schema: "ContractSchema"
    ) -> "ArchitectOutput":
        prompt = (
            "Write a Python 3.12 function `calculate(**kwargs)` using decimal.Decimal "
            "with explicit quantization and an explicit decimal.Context (truncation = "
            "ROUND_DOWN, COBOL ROUNDED = ROUND_HALF_UP). No floats, no imports other "
            "than decimal. Also produce dashboard metadata. Return JSON: "
            '{"verified_python_code": str, "dashboard_metadata": object}.\n\n'
            f"Contract: {_dumps(schema)}\nRules: {_dumps(rules)}\n\nCOBOL:\n{cobol_source}"
        )
        return self._architect("cloud_architect", prompt)

    def repair_python(
        self,
        cobol_source: str,
        rules: "ParsedRules",
        schema: "ContractSchema",
        counterexample: Any,
        previous_code: str,
    ) -> "ArchitectOutput":
        prompt = (
            "The Python below disagrees with the real COBOL on the counterexample. "
            "Fix it (same rules as before). Return JSON: "
            '{"verified_python_code": str, "dashboard_metadata": object}.\n\n'
            f"Counterexample: {_dumps(counterexample)}\n"
            f"Contract: {_dumps(schema)}\nRules: {_dumps(rules)}\n\n"
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
        return ArchitectOutput(verified_python_code=code, dashboard_metadata=meta)

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
            raise BudgetExceeded("LLM budget exhausted")

        try:
            from openai import OpenAI

            client = OpenAI(
                base_url=self.config.base_url or None,
                api_key=self.config.api_key,
                timeout=TIMEOUT_SECONDS,
                max_retries=0,
            )
            resp = client.chat.completions.create(
                model=self.config.model,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
            )
        except Exception:  # noqa: BLE001 - normalized; raw error may hold secrets
            raise LLMCallError(stage, "LLM request failed or timed out") from None

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
            return json.loads(text, parse_float=Decimal)
        except (ValueError, RecursionError):
            raise LLMCallError(stage, "LLM response was not valid JSON") from None
