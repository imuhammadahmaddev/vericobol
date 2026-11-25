"""Resurrection pipeline: data models and contract-derived metadata.

The input/output contract of a program comes only from the preset catalog or
from the caller's ``field_schema``. Anything the LLM says about names, bounds or
steps is discarded and rebuilt from the resolved ``ContractSchema``.

Later tasks append ``SelfHealingLoop`` and ``Pipeline`` to this module.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
import textwrap
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Mapping

from .catalog import PRESET_ORDER, load_presets
from .cobol_runner import CobolRunner
from .export_template import render
from .pipeline_cache import CachedResult, PipelineCache, cache_key
from .pbt_verifier import Counterexample, ParityReport, VerificationError, run_verification

PRESET_IDS: tuple[str, ...] = PRESET_ORDER

ROUND_DOWN = "ROUND_DOWN"          # COBOL default: truncation
ROUND_HALF_UP = "ROUND_HALF_UP"    # COBOL ROUNDED

StageName = Literal["legacy_parser", "cloud_architect", "parity_auditor"]
StageStatus = Literal["ok", "failed", "skipped"]
StageSource = Literal["llm", "cache", "deterministic"]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class PipelineStageError(Exception):
    """An HTTP-mappable pipeline failure: status code, stage name and body."""

    def __init__(self, status: int, stage: str | None, body: Any):
        super().__init__(f"{stage or 'pipeline'} failed with status {status}")
        self.status = status
        self.stage = stage
        self.body = body


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FieldContract:
    name: str
    pic: str
    scale: int
    direction: Literal["input", "output"]
    min: Decimal | None = None
    max: Decimal | None = None
    step: Decimal | None = None
    label: str = ""
    rounding: str = ROUND_DOWN


@dataclass(frozen=True)
class ContractSchema:
    input_fields: tuple[FieldContract, ...]
    output_fields: tuple[FieldContract, ...]

    @property
    def all_fields(self) -> tuple[FieldContract, ...]:
        return self.input_fields + self.output_fields


_PIC_RE = re.compile(r"^(S)?((?:9(?:\(\d+\))?)+)(?:V((?:9(?:\(\d+\))?)+))?$", re.IGNORECASE)
_NINES_RE = re.compile(r"9(?:\((\d+)\))?")


def _count_nines(part: str) -> int:
    return sum(int(m.group(1)) if m.group(1) else 1 for m in _NINES_RE.finditer(part))


def parse_pic(pic: str) -> tuple[bool, int, int]:
    """Return (signed, integer_digits, scale) for a numeric PIC such as ``S9(9)V99``."""
    match = _PIC_RE.match(pic.strip().replace(" ", ""))
    if match is None:
        raise ValueError(f"unsupported numeric PIC clause: {pic!r}")
    signed = match.group(1) is not None
    int_digits = _count_nines(match.group(2))
    scale = _count_nines(match.group(3)) if match.group(3) else 0
    return signed, int_digits, scale


def _to_decimal(value: Any, what: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError(f"{what} must be a string, integer or Decimal")
    if isinstance(value, (Decimal, int, str)):
        try:
            return Decimal(value)
        except ArithmeticError:
            raise ValueError(f"{what} is not a valid decimal: {value!r}") from None
    # Binary floats land here on purpose: money bounds are never floats.
    raise TypeError(f"{what} must be a string, integer or Decimal, not {type(value).__name__}")


def _unit(scale: int) -> Decimal:
    return Decimal(1).scaleb(-scale)


def _pic_bounds(pic: str) -> tuple[Decimal, Decimal, Decimal]:
    signed, int_digits, scale = parse_pic(pic)
    top = Decimal(10 ** (int_digits + scale) - 1).scaleb(-scale)
    return (-top if signed else Decimal(0).scaleb(-scale)), top, _unit(scale)


def contract_from_field_schema(
    fields: Iterable[FieldContract | Mapping[str, Any]],
) -> ContractSchema:
    """Build a ContractSchema from caller-supplied field descriptions.

    Each item is a ``FieldContract`` or a mapping with ``name``, ``pic`` and
    ``direction`` (required) and optional ``scale``, ``min``, ``max``, ``step``,
    ``label``, ``rounding``. A missing ``scale`` comes from the PIC clause. Input
    fields without bounds get the PIC capacity as min/max and one unit as step.
    Binary floats are rejected.
    """
    inputs: list[FieldContract] = []
    outputs: list[FieldContract] = []
    seen: set[str] = set()
    for index, raw in enumerate(fields):
        item = (
            raw
            if isinstance(raw, FieldContract)
            else FieldContract(
                name=str(raw.get("name") or ""),
                pic=str(raw.get("pic") or ""),
                scale=raw["scale"] if raw.get("scale") is not None else -1,
                direction=raw.get("direction") or "",  # type: ignore[arg-type]
                min=_to_decimal(raw.get("min"), f"field_schema[{index}].min"),
                max=_to_decimal(raw.get("max"), f"field_schema[{index}].max"),
                step=_to_decimal(raw.get("step"), f"field_schema[{index}].step"),
                label=str(raw.get("label") or ""),
                rounding=str(raw.get("rounding") or ROUND_DOWN),
            )
        )
        for attr in ("name", "pic", "direction"):
            if not getattr(item, attr):
                raise ValueError(f"field_schema[{index}] is missing {attr}")
        if item.direction not in ("input", "output"):
            raise ValueError(f"field_schema[{index}].direction must be 'input' or 'output'")
        if item.rounding not in (ROUND_DOWN, ROUND_HALF_UP):
            raise ValueError(f"field_schema[{index}].rounding is not supported: {item.rounding!r}")
        if item.name in seen:
            raise ValueError(f"field_schema[{index}] duplicates field name {item.name!r}")
        seen.add(item.name)

        _, _, pic_scale = parse_pic(item.pic)
        scale = pic_scale if item.scale < 0 else item.scale
        lo, hi, step = item.min, item.max, item.step
        if item.direction == "input":
            d_lo, d_hi, d_step = _pic_bounds(item.pic)
            lo = d_lo if lo is None else lo
            hi = d_hi if hi is None else hi
            step = _unit(scale) if step is None else step
        resolved = FieldContract(
            name=item.name,
            pic=item.pic,
            scale=scale,
            direction=item.direction,
            min=lo,
            max=hi,
            step=step,
            label=item.label or item.name.replace("_", " ").title(),
            rounding=item.rounding,
        )
        (inputs if resolved.direction == "input" else outputs).append(resolved)

    if not inputs or not outputs:
        raise ValueError("field_schema needs at least one input and one output field")
    return ContractSchema(tuple(inputs), tuple(outputs))


# PIC clauses of the preset programs (the catalog stores bounds, not PICs).
# Outputs also record how the COBOL program stores them (ROUNDED or truncation).
_PRESET_INPUT_PICS: dict[str, dict[str, str]] = {
    "loan_calc": {
        "principal": "9(9)V99",
        "annual_rate_pct": "9(3)V999",
        "term_months": "9(3)",
    },
    "compound_int": {
        "principal": "9(9)V99",
        "annual_rate_pct": "9(3)V999",
        "compounds_per_year": "9(2)",
        "years": "9(2)",
    },
    "daily_ledger": {
        "opening_balance": "S9(9)V99",
        "deposits": "9(9)V99",
        "withdrawals": "9(9)V99",
        "overdraft_threshold": "S9(9)V99",
        "overdraft_fee": "9(5)V99",
    },
}

_PRESET_OUTPUTS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "loan_calc": (
        ("monthly_payment", "9(9)V99", ROUND_HALF_UP),
        ("total_interest", "9(9)V99", ROUND_HALF_UP),
        ("total_payable", "9(9)V99", ROUND_HALF_UP),
    ),
    "compound_int": (
        ("maturity_amount", "9(9)V99", ROUND_HALF_UP),
        ("accrued_interest", "S9(9)V99", ROUND_HALF_UP),
    ),
    "daily_ledger": (
        ("closing_balance", "S9(9)V99", ROUND_DOWN),
        ("fee_applied", "9(5)V99", ROUND_DOWN),
        ("overdraft_flag", "9(1)", ROUND_DOWN),
    ),
}


def contract_from_preset(preset_id: str) -> ContractSchema:
    """Resolve a preset's contract from the static catalog."""
    if preset_id not in PRESET_IDS:
        raise ValueError(f"unknown preset_id {preset_id!r}; valid ids: {', '.join(PRESET_IDS)}")
    entry = next(p for p in load_presets() if p["preset_id"] == preset_id)
    pics = _PRESET_INPUT_PICS[preset_id]
    fields: list[dict[str, Any]] = [
        {
            "name": f["name"],
            "pic": pics[f["name"]],
            "direction": "input",
            "min": f["min"],
            "max": f["max"],
            "step": f["step"],
            "label": f.get("label", ""),
        }
        for f in entry["input_schema"]
    ]
    fields += [
        {"name": name, "pic": pic, "direction": "output", "rounding": rounding}
        for name, pic, rounding in _PRESET_OUTPUTS[preset_id]
    ]
    return contract_from_field_schema(fields)


# ---------------------------------------------------------------------------
# LLM-facing and response models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ParsedRules:
    business_rules: list[str]
    pic_mappings: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class ArchitectOutput:
    verified_python_code: str
    dashboard_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StageRecord:
    name: StageName
    status: StageStatus
    elapsed_ms: int
    source: StageSource

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "elapsed_ms": self.elapsed_ms,
            "source": self.source,
        }


@dataclass(frozen=True)
class AttemptRecord:
    attempt: int
    parity_score_pct: str
    passed_cases: int
    total_cases: int
    counterexample: Counterexample | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "parity_score_pct": self.parity_score_pct,
            "passed_cases": self.passed_cases,
            "total_cases": self.total_cases,
            "counterexample": None if self.counterexample is None else self.counterexample.to_dict(),
        }


@dataclass(frozen=True)
class FinalReport:
    report: ParityReport
    certified: bool
    status: Literal["passed", "failed"]

    def to_dict(self) -> dict[str, Any]:
        return {**self.report.to_dict(), "certified": self.certified, "status": self.status}


@dataclass(frozen=True)
class ResurrectResponse:
    preset_id: str | None
    cache_hit: bool
    llm_used: bool
    stages: list[StageRecord]
    business_rules: list[str]
    pic_mappings: list[dict[str, str]]
    dashboard_metadata: dict[str, Any]
    verified_python_code: str
    export_fastapi_code: str
    audit_trail: list[AttemptRecord]
    final_report: FinalReport

    def to_dict(self) -> dict[str, Any]:
        """Response body for ``DecimalJSONResponse``. Decimals are kept as Decimal."""
        return {
            "preset_id": self.preset_id,
            "cache_hit": self.cache_hit,
            "llm_used": self.llm_used,
            "stages": [s.to_dict() for s in self.stages],
            "business_rules": list(self.business_rules),
            "pic_mappings": [dict(m) for m in self.pic_mappings],
            "dashboard_metadata": self.dashboard_metadata,
            "verified_python_code": self.verified_python_code,
            "export_fastapi_code": self.export_fastapi_code,
            "audit_trail": [a.to_dict() for a in self.audit_trail],
            "final_report": self.final_report.to_dict(),
        }


# ---------------------------------------------------------------------------
# Contract-derived metadata (LLM values for names, bounds and steps are ignored)
# ---------------------------------------------------------------------------

_OUTPUT_FORMATS = frozenset({"currency", "integer", "decimal", "flag", "percent"})
_MAX_LABEL = 80


def _llm_entries(llm_metadata: Mapping[str, Any] | None, key: str) -> dict[str, Mapping[str, Any]]:
    entries = (llm_metadata or {}).get(key)
    found: dict[str, Mapping[str, Any]] = {}
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, Mapping) and isinstance(entry.get("name"), str):
                found.setdefault(entry["name"], entry)
    return found


def _llm_label(entry: Mapping[str, Any] | None, default: str) -> str:
    label = (entry or {}).get("label")
    if isinstance(label, str) and 0 < len(label.strip()) <= _MAX_LABEL:
        return label.strip()
    return default


def _default_format(f: FieldContract) -> str:
    _, int_digits, _ = parse_pic(f.pic)
    if f.scale == 0:
        return "flag" if int_digits == 1 else "integer"
    return "currency" if f.scale == 2 else "decimal"


def build_dashboard_metadata(
    schema: ContractSchema, llm_metadata: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Dashboard JSON whose names, bounds and steps come only from ``schema``.

    From ``llm_metadata`` only the display label (and a known output format) of a
    field that exists in the schema is kept. Invented fields are dropped.
    """
    llm_inputs = _llm_entries(llm_metadata, "inputs")
    llm_outputs = _llm_entries(llm_metadata, "outputs")
    inputs = [
        {
            "name": f.name,
            "label": _llm_label(llm_inputs.get(f.name), f.label),
            "type": "integer" if f.scale == 0 else "decimal",
            "min": f.min,
            "max": f.max,
            "step": f.step,
        }
        for f in schema.input_fields
    ]
    outputs = []
    for f in schema.output_fields:
        fmt = (llm_outputs.get(f.name) or {}).get("format")
        outputs.append(
            {
                "name": f.name,
                "label": _llm_label(llm_outputs.get(f.name), f.label),
                "format": fmt if fmt in _OUTPUT_FORMATS else _default_format(f),
            }
        )
    return {"inputs": inputs, "outputs": outputs}


def build_pic_mappings(
    schema: ContractSchema, llm_mappings: Iterable[Mapping[str, Any]] | None = None
) -> list[dict[str, str]]:
    """One mapping per schema field, in schema order, with every value from ``schema``.

    ``llm_mappings`` is accepted so callers can pass the parser output, but it
    cannot add, remove or change an entry.
    """
    del llm_mappings
    return [
        {
            "name": f.name,
            "pic": f.pic,
            "scale": str(f.scale),
            "rounding": f.rounding,
        }
        for f in schema.all_fields
    ]


# ---------------------------------------------------------------------------
# Self-healing loop (Parity_Auditor)
# ---------------------------------------------------------------------------

def _verification_error_status(exc: VerificationError, custom: bool) -> int:
    """Same mapping as ``main._verify_blocking``."""
    if exc.kind == "rejected":
        return 400
    if exc.kind == "run_timeout":
        return 504
    if exc.kind in ("compile_failed", "invalid_request"):
        return 422
    return 422 if custom else 500


class SelfHealingLoop:
    """Verify, and on a parity failure repair with the first counterexample.

    At most ``1 + MAX_RETRIES`` calls to ``run_verification``, all with the same
    COBOL source. A ``VerificationError`` is never retried: it becomes a
    ``PipelineStageError``. A parity failure is a normal report, not an exception.
    """

    MAX_RETRIES = 2

    def __init__(self, llm: Any, runner: CobolRunner | None = None):
        self._llm = llm
        self._runner = runner

    def run(
        self,
        cobol_source: str | None,
        preset_id: str | None,
        rules: ParsedRules,
        schema: ContractSchema,
        first_attempt_code: str,
        first_attempt_source: StageSource,
        num_cases: int,
        *,
        first_attempt_float_bug: bool = False,
        repair_fn: Callable[[Counterexample, str], str] | None = None,
        max_attempts: int | None = None,
        llm_source: str | None = None,
        layout: str | None = None,
    ) -> tuple[list[AttemptRecord], FinalReport, str]:
        """Return ``(audit_trail, final_report, final_code)``.

        Pipeline hooks: ``first_attempt_float_bug`` verifies attempt 1 through the
        float-bug demonstration model (the restricted sandbox has no ``float``),
        ``repair_fn`` replaces the LLM repair, ``max_attempts`` caps the attempts
        (1 = never repair) and ``llm_source`` is the COBOL text shown to the LLM
        when ``cobol_source`` is None (presets).
        """
        del first_attempt_source  # stage bookkeeping belongs to the Pipeline
        audit_trail: list[AttemptRecord] = []
        code = first_attempt_code
        last_attempt = 1 + self.MAX_RETRIES
        if max_attempts is not None:
            last_attempt = max(1, min(last_attempt, max_attempts))
        for attempt in range(1, last_attempt + 1):
            float_bug = first_attempt_float_bug and attempt == 1
            try:
                report = run_verification(
                    layout or preset_id,
                    cobol_source,
                    num_cases=num_cases,
                    python_code=None if float_bug else code,
                    simulate_float_bug=float_bug,
                    runner=self._runner,
                )
            except VerificationError as exc:
                raise PipelineStageError(
                    _verification_error_status(exc, custom=preset_id is None or cobol_source is not None),
                    "parity_auditor",
                    {"error": exc.kind, "message": exc.message},
                ) from exc

            first_cx = report.counterexamples[0] if report.counterexamples else None
            audit_trail.append(
                AttemptRecord(
                    attempt=attempt,
                    parity_score_pct=report.parity_score_pct,
                    passed_cases=report.passed_cases,
                    total_cases=report.total_cases,
                    counterexample=None if report.verified else first_cx,
                )
            )
            if report.verified:
                return audit_trail, FinalReport(report, True, "passed"), code
            if attempt == last_attempt:
                return audit_trail, FinalReport(report, False, "failed"), code
            if first_cx is None:  # failed without a counterexample: nothing to repair from
                return audit_trail, FinalReport(report, False, "failed"), code
            if repair_fn is not None:
                code = repair_fn(first_cx, code)
            else:
                repaired = self._llm.repair_python(
                    llm_source if llm_source is not None else cobol_source,
                    rules, schema, first_cx, code,
                )
                code = repaired.verified_python_code
        raise AssertionError("unreachable")  # pragma: no cover


# ---------------------------------------------------------------------------
# Reference (Decimal) source for Demo_Mode and the preset bundles
# ---------------------------------------------------------------------------

_SAMPLES_DIR = Path(__file__).resolve().parent.parent / "cobol_samples"
BUNDLES_PATH = _SAMPLES_DIR / "preset_bundles.json"

# Replaces ``reference_models._scaled_trunc``: the sandbox has no isinstance/map/str.
_SANDBOX_PRELUDE = (
    "_COB_MAX_DIGITS = 38\n\n"
    "def _scaled_trunc(value, scale):\n"
    "    return int(value.scaleb(scale))\n"
)
_REFERENCE_HELPERS = (
    "_store_unsigned", "_store_signed", "_wrap_unsigned", "_wrap_signed",
    "_cob_div", "_round_half_up", "_to_decimal",
)


class _SandboxFriendly(ast.NodeTransformer):
    """Drop annotations and docstrings, and unwrap ``with localcontext(...)``."""

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        self.generic_visit(node)
        node.returns = None
        for arg in node.args.args + node.args.kwonlyargs:
            arg.annotation = None
        if (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
            and len(node.body) > 1
        ):
            node.body = node.body[1:]
        return node

    def visit_With(self, node: ast.With) -> list[ast.stmt]:
        self.generic_visit(node)
        return node.body


def reference_decimal_source(preset_id: str) -> str:
    """Self-contained, sandbox-loadable Decimal source of a preset's reference model.

    Built from ``inspect.getsource`` of the ``REGISTRY`` function and its helpers,
    with a ``calculate`` entry point appended. Contains no float.
    """
    from . import reference_models

    if preset_id not in reference_models.REGISTRY:
        raise ValueError(f"unknown preset_id {preset_id!r}")
    fn = reference_models.REGISTRY[preset_id]
    parts = [_SANDBOX_PRELUDE]
    for func in [getattr(reference_models, n) for n in _REFERENCE_HELPERS] + [fn]:
        tree = _SandboxFriendly().visit(ast.parse(textwrap.dedent(inspect.getsource(func))))
        parts.append(ast.unparse(ast.fix_missing_locations(tree)) + "\n")
    parts.append(f"def calculate(**kwargs):\n    return {fn.__name__}(**kwargs)\n")
    return "\n".join(parts)


def _preset_source(preset_id: str) -> str:
    return (_SAMPLES_DIR / f"{preset_id}.cbl").read_text(encoding="utf-8")


def _reference_rules(schema: ContractSchema) -> ParsedRules:
    rules = [
        f"{f.name} is stored in PIC {f.pic} and "
        + ("rounded half-up (COBOL ROUNDED)." if f.rounding == ROUND_HALF_UP else "truncated (COBOL default).")
        for f in schema.output_fields
    ]
    return ParsedRules(business_rules=rules, pic_mappings=build_pic_mappings(schema))


# ---------------------------------------------------------------------------
# Preset bundle file
#
# preset_bundles.json = {preset_id: {"false": Entry, "true": Entry}} where the
# key is ``demonstrate_self_healing`` as a string and Entry is
#   {"business_rules": [str], "pic_mappings": [{name,pic,scale,rounding}],
#    "dashboard_metadata": {...}, "verified_python_code": str,
#    "export_fastapi_code": str,
#    "stages": [{"name","status","elapsed_ms","source": "cache"}, ...]}
# ---------------------------------------------------------------------------

def load_preset_bundles(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Parsed bundle file, or ``{}`` when it is missing or unreadable."""
    try:
        text = (path or BUNDLES_PATH).read_text(encoding="utf-8")
        data = json.loads(text, parse_float=Decimal)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _cached_from_entry(entry: Mapping[str, Any]) -> CachedResult:
    return CachedResult(
        business_rules=[str(r) for r in entry["business_rules"]],
        pic_mappings=[dict(m) for m in entry["pic_mappings"]],
        dashboard_metadata=dict(entry["dashboard_metadata"]),
        verified_python_code=str(entry["verified_python_code"]),
        export_fastapi_code=str(entry["export_fastapi_code"]),
        stages=list(entry.get("stages", [])),
    )


def seed_cache(cache: PipelineCache, bundles: Mapping[str, Any]) -> None:
    """Load every well-formed bundle entry into ``cache`` (existing keys win)."""
    for preset_id in PRESET_IDS:
        for flag in ("false", "true"):
            try:
                entry = bundles[preset_id][flag]
                key = cache_key(_preset_source(preset_id), flag == "true")
                if key not in cache:
                    cache.put(key, _cached_from_entry(entry))
            except (KeyError, TypeError, OSError):
                continue


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def _ms(start: float) -> int:  # float-ok: timing
    return max(0, int((time.perf_counter() - start) * 1000))


class Pipeline:
    """Legacy_Parser -> Cloud_Architect -> Parity_Auditor orchestration."""

    def __init__(
        self,
        llm: Any,
        cache: PipelineCache,
        bundles: Mapping[str, Any] | None = None,
        runner: CobolRunner | None = None,
    ):
        self._llm = llm
        self._cache = cache
        self._runner = runner
        seed_cache(cache, load_preset_bundles() if bundles is None else bundles)

    def run(
        self,
        *,
        preset_id: str | None,
        cobol_source: str | None,
        schema: ContractSchema,
        num_cases: int,
        demonstrate_self_healing: bool = False,
        force_live_llm: bool = False,
        layout: str | None = None,
    ) -> ResurrectResponse:
        if preset_id is not None:
            if preset_id not in PRESET_IDS:
                raise PipelineStageError(
                    404, None, {"error": "unknown_preset", "valid_ids": list(PRESET_IDS)}
                )
            source = _preset_source(preset_id)
            verify_source = None  # the bundled preset binary is used
        elif cobol_source is not None:
            source = verify_source = cobol_source
        else:
            raise PipelineStageError(
                422, None, {"error": "invalid_request", "message": "preset_id or cobol_code required"}
            )
        if demonstrate_self_healing and preset_id is None:
            raise PipelineStageError(
                422,
                None,
                {"error": "invalid_request", "message": "demonstration mode requires a preset_id"},
            )

        key = cache_key(source, demonstrate_self_healing)
        cached = None
        if preset_id is not None and not force_live_llm:
            cached = self._cache.get(key)
        loop = SelfHealingLoop(self._llm, self._runner)
        stages: list[StageRecord] = []
        llm_used = False

        if demonstrate_self_healing:
            t0 = time.perf_counter()
            if cached is not None:
                rules = ParsedRules(cached.business_rules, cached.pic_mappings)
                metadata, decimal_code = cached.dashboard_metadata, cached.verified_python_code
                stage_source: StageSource = "cache"
            else:
                rules = _reference_rules(schema)
                metadata = build_dashboard_metadata(schema)
                decimal_code = reference_decimal_source(preset_id)  # type: ignore[arg-type]
                stage_source = "deterministic"
            elapsed = _ms(t0)
            stages += [
                StageRecord("legacy_parser", "ok", elapsed, stage_source),
                StageRecord("cloud_architect", "ok", 0, stage_source),
            ]
            t1 = time.perf_counter()
            # Attempt 1: the float-bug model (FLOAT_BUG_REGISTRY). Attempt 2: the Decimal source.
            trail, final, code = loop.run(
                verify_source, preset_id, rules, schema, decimal_code, "deterministic", num_cases,
                first_attempt_float_bug=True,
                repair_fn=lambda _cx, _code: decimal_code,
                max_attempts=2,
            )
            audit_source: StageSource = "deterministic"
        elif cached is not None:
            rules = ParsedRules(cached.business_rules, cached.pic_mappings)
            metadata = cached.dashboard_metadata
            stages += [
                StageRecord("legacy_parser", "ok", 0, "cache"),
                StageRecord("cloud_architect", "ok", 0, "cache"),
            ]
            t1 = time.perf_counter()
            trail, final, code = loop.run(
                verify_source, preset_id, rules, schema, cached.verified_python_code, "cache",
                num_cases, max_attempts=1,  # a stale entry is never repaired
            )
            audit_source = "cache"
        else:
            self._llm.require_key()
            llm_used = True
            t0 = time.perf_counter()
            rules = self._llm.extract_rules(source)
            stages.append(StageRecord("legacy_parser", "ok", _ms(t0), "llm"))
            t0 = time.perf_counter()
            architect = self._llm.generate_python(source, rules, schema)
            stages.append(StageRecord("cloud_architect", "ok", _ms(t0), "llm"))
            metadata = architect.dashboard_metadata
            t1 = time.perf_counter()
            trail, final, code = loop.run(
                verify_source, preset_id, rules, schema, architect.verified_python_code, "llm",
                num_cases, llm_source=source, layout=layout,
            )
            audit_source = "llm" if len(trail) > 1 else "deterministic"

        stages.append(
            StageRecord("parity_auditor", "ok" if final.certified else "failed", _ms(t1), audit_source)
        )

        if cached is not None:
            business_rules, pic_mappings = list(cached.business_rules), cached.pic_mappings
            dashboard = cached.dashboard_metadata
            export_code = cached.export_fastapi_code if code == cached.verified_python_code else render(code, schema)
        else:
            business_rules = list(rules.business_rules)
            pic_mappings = build_pic_mappings(schema, rules.pic_mappings)
            dashboard = build_dashboard_metadata(schema, metadata)
            export_code = render(code, schema)

        if cached is None and preset_id is not None and final.certified:
            self._cache.put(
                key,
                CachedResult(
                    business_rules=business_rules,
                    pic_mappings=pic_mappings,
                    dashboard_metadata=dashboard,
                    verified_python_code=code,
                    export_fastapi_code=export_code,
                    stages=[StageRecord(s.name, "ok", 0, "cache") for s in stages[:2]],
                ),
            )

        return ResurrectResponse(
            preset_id=preset_id,
            cache_hit=cached is not None,
            llm_used=llm_used,
            stages=stages,
            business_rules=business_rules,
            pic_mappings=pic_mappings,
            dashboard_metadata=dashboard,
            verified_python_code=code,
            export_fastapi_code=export_code,
            audit_trail=trail,
            final_report=final,
        )
