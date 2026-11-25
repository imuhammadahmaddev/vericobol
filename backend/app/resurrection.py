"""Universal resurrection pipeline: Legacy_Parser -> Cloud_Architect -> Parity_Auditor.

Every program, including the starter templates, goes through the live LLM
stages (or a Certified Pipeline_Cache hit) and a live GnuCOBOL verification.
Nothing here reads ``preset_bundles.json`` or imports ``reference_models``.
The contract comes from ``LLMClient.infer_contract``; the names, bounds and
steps shown on the dashboard are rebuilt from that contract.
"""

from __future__ import annotations

import ast
import dataclasses
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Iterable, Literal, Mapping

from .cobol_runner import CobolRunner, validate_source
from .export_template import render
from .llm_client import InferredProgram, LLMCallError, LLMUnavailable
from .pbt_verifier import (
    Counterexample,
    ParityReport,
    VerificationError,
    _shared_runner,
    run_universal_verification,
)
from .pipeline_cache import CachedResult, PipelineCache, cache_key

ROUND_DOWN = "ROUND_DOWN"          # COBOL default: truncation
ROUND_HALF_UP = "ROUND_HALF_UP"    # COBOL ROUNDED

StageName = Literal["legacy_parser", "cloud_architect", "parity_auditor"]
StageStatus = Literal["ok", "failed", "skipped"]
StageSource = Literal["llm", "cache", "live", "deterministic"]


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


def _llm_unavailable_error(exc: LLMUnavailable, stage: str) -> PipelineStageError:
    # str(exc) is a fixed message (never the key or a provider payload).
    return PipelineStageError(
        503,
        stage,
        {
            "error": "llm_unavailable",
            "cause": exc.cause,
            "message": f"The LLM is unavailable: {exc}.",
        },
    )


def _llm_call_error(exc: LLMCallError) -> PipelineStageError:
    return PipelineStageError(
        502, exc.stage, {"error": "llm_call_failed", "stage": exc.stage, "message": exc.message[:500]}
    )


# ---------------------------------------------------------------------------
# Contract (typed view used by export_template.render)
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

    Kept for the legacy ``field_schema`` request shape. Each item is a
    ``FieldContract`` or a mapping with ``name``, ``pic`` and ``direction``
    (required) and optional ``scale``, ``min``, ``max``, ``step``, ``label``,
    ``rounding``. Binary floats are rejected.
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
            d_lo, d_hi, _ = _pic_bounds(item.pic)
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


def _norm_rounding(value: Any) -> str:
    text = str(value or "").upper().replace("-", "_").replace(" ", "_")
    return ROUND_HALF_UP if text in ("ROUND_HALF_UP", "ROUNDED", "HALF_UP") else ROUND_DOWN


def _fmt_dec(value: Decimal) -> str:
    return format(value, "f")


def normalize_contract(contract: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Copy of an inferred contract with every input bound filled in.

    Missing ``min``/``max`` come from the PIC capacity and a missing ``step``
    is one unit of the field scale. Output rounding is normalized to
    ``ROUND_DOWN`` or ``ROUND_HALF_UP``. Values stay strings.
    """
    inputs: list[dict[str, Any]] = []
    for f in contract.get("inputs", []):
        item = dict(f)
        scale = int(item["scale"])
        try:
            lo, hi, _ = _pic_bounds(str(item["pic"]))
        except ValueError:
            lo = hi = None
        if item.get("min") is None and lo is not None:
            item["min"] = _fmt_dec(lo.quantize(_unit(scale)) if scale >= 0 else lo)
        if item.get("max") is None and hi is not None:
            item["max"] = _fmt_dec(hi.quantize(_unit(scale)))
        if item.get("step") is None:
            item["step"] = _fmt_dec(_unit(scale))
        inputs.append(item)
    outputs = [{**dict(f), "rounding": _norm_rounding(f.get("rounding"))}
               for f in contract.get("outputs", [])]
    return {"inputs": inputs, "outputs": outputs}


def schema_from_contract(contract: Mapping[str, Any]) -> ContractSchema:
    """Typed ContractSchema from an inferred (normalized) contract."""

    def build(f: Mapping[str, Any], direction: Literal["input", "output"]) -> FieldContract:
        return FieldContract(
            name=str(f["name"]),
            pic=str(f["pic"]),
            scale=int(f["scale"]),
            direction=direction,
            min=_to_decimal(f.get("min"), "min"),
            max=_to_decimal(f.get("max"), "max"),
            step=_to_decimal(f.get("step"), "step"),
            label=str(f.get("label") or f["name"]),
            rounding=_norm_rounding(f.get("rounding")),
        )

    return ContractSchema(
        tuple(build(f, "input") for f in contract.get("inputs", [])),
        tuple(build(f, "output") for f in contract.get("outputs", [])),
    )


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
    cobol_code: str
    program_title: str
    description: str
    io_protocol: str
    contract: dict[str, list[dict[str, Any]]]
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
    preset_id: str | None = None

    @property
    def certified(self) -> bool:
        return self.final_report.certified

    def to_dict(self) -> dict[str, Any]:
        """Response body for ``DecimalJSONResponse``."""
        return {
            "preset_id": self.preset_id,
            "cobol_code": self.cobol_code,
            "program_title": self.program_title,
            "description": self.description,
            "io_protocol": self.io_protocol,
            "contract": self.contract,
            "cache_hit": self.cache_hit,
            "llm_used": self.llm_used,
            "stages": [s.to_dict() for s in self.stages],
            "business_rules": list(self.business_rules),
            "pic_mappings": [dict(m) for m in self.pic_mappings],
            "dashboard_metadata": self.dashboard_metadata,
            "verified_python_code": self.verified_python_code,
            "export_fastapi_code": self.export_fastapi_code,
            "audit_trail": [a.to_dict() for a in self.audit_trail],
            "parity_report": self.final_report.report.to_dict(),
            "final_report": self.final_report.to_dict(),
            "certified": self.certified,
        }


# ---------------------------------------------------------------------------
# Contract-derived metadata
# ---------------------------------------------------------------------------

_MAX_TITLE = 120
_CHART_TYPES = ("bar_comparison", "breakdown", "waterfall", "summary")


def build_dashboard_metadata(program: InferredProgram) -> dict[str, Any]:
    """Dashboard JSON for any program.

    Field names, labels, bounds, steps and formats come from the inferred
    contract. Only the title and a known chart type are taken from the LLM's
    ``dashboard_metadata``.
    """
    llm_meta = program.dashboard_metadata or {}
    title = llm_meta.get("title")
    chart = llm_meta.get("chart_type")
    contract = normalize_contract(program.contract)
    return {
        "title": title.strip()[:_MAX_TITLE] if isinstance(title, str) and title.strip()
        else program.program_title,
        "chart_type": chart if chart in _CHART_TYPES else "summary",
        "inputs": [
            {
                "name": f["name"],
                "label": f["label"],
                "type": "integer" if int(f["scale"]) == 0 else "decimal",
                "min": f.get("min"),
                "max": f.get("max"),
                "step": f.get("step"),
            }
            for f in contract["inputs"]
        ],
        "outputs": [
            {"name": f["name"], "label": f["label"], "format": f["format"]}
            for f in contract["outputs"]
        ],
    }


def build_pic_mappings(schema: ContractSchema) -> list[dict[str, str]]:
    """One mapping per schema field, in schema order."""
    return [
        {"name": f.name, "pic": f.pic, "scale": str(f.scale), "rounding": f.rounding}
        for f in schema.all_fields
    ]


# ---------------------------------------------------------------------------
# Float guard (generated code outside the float demo attempt)
# ---------------------------------------------------------------------------

def find_float_construct(code: str) -> str | None:
    """Describe the first float construct in ``code`` or return None.

    Looks for the ``float`` name, float literals, ``math.pow`` and ``**`` with a
    float literal operand. Code that does not parse is left to the verifier.
    """
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError):
        return None
    for node in ast.walk(tree):
        line = getattr(node, "lineno", "?")
        if isinstance(node, ast.Name) and node.id == "float":
            return f"float type used (line {line})"
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            return f"float literal used (line {line})"
        if isinstance(node, ast.Attribute) and node.attr == "pow" \
                and isinstance(node.value, ast.Name) and node.value.id == "math":
            return f"math.pow used (line {line})"
        if isinstance(node, ast.ImportFrom) and node.module == "math" \
                and any(a.name == "pow" for a in node.names):
            return f"math.pow imported (line {line})"
    return None


# ---------------------------------------------------------------------------
# Self-healing loop (Parity_Auditor)
# ---------------------------------------------------------------------------

def _verification_error(exc: VerificationError) -> PipelineStageError:
    status = {
        "rejected": 400,
        "compile_failed": 400,
        "run_timeout": 504,
        "invalid_request": 422,
    }.get(exc.kind, 422)
    return PipelineStageError(
        status, "parity_auditor", {"error": exc.kind, "message": exc.message}
    )


class SelfHealingLoop:
    """Verify, and on a failure repair from the first Counterexample.

    At most ``MAX_RETRIES`` repairs. Generated code with float constructs is
    never verified: it counts as a failed attempt (except in the demo attempt).
    A ``VerificationError`` is not retried; it becomes a ``PipelineStageError``.
    """

    MAX_RETRIES = 2

    def __init__(self, llm: Any, runner: CobolRunner | None = None):
        self._llm = llm
        self._runner = runner

    def run(
        self,
        cobol_source: str,
        program: InferredProgram,
        first_code: str,
        num_cases: int,
        *,
        first_attempt_is_float_demo: bool = False,
        repair_fn: Callable[[Counterexample, str], str] | None = None,
        exact_fn: Callable[[], str] | None = None,
    ) -> tuple[list[AttemptRecord], FinalReport, str]:
        """Return ``(audit_trail, final_report, final_code)``.

        ``repair_fn`` and ``exact_fn`` replace the LLM calls (used to replay a
        cached demo). ``exact_fn`` supplies exact Decimal code when a float demo
        attempt happened to match COBOL exactly.
        """
        trail: list[AttemptRecord] = []
        code = first_code
        float_attempt = first_attempt_is_float_demo
        retries = 0
        attempt = 0
        while True:
            attempt += 1
            guard = None if float_attempt else find_float_construct(code)
            if guard is not None:
                cx = Counterexample(
                    inputs={}, cobol_output={}, python_output={"error": guard},
                    differing_field="python_code", penny_delta="0", likely_cause="python_error",
                )
                report = ParityReport.build(0, 1, 0, [], [cx])
            else:
                try:
                    report = run_universal_verification(
                        cobol_source,
                        program.contract,
                        program.io_protocol,
                        code,
                        num_cases=num_cases,
                        allow_float_demo=float_attempt,
                        runner=self._runner,
                    )
                except VerificationError as exc:
                    raise _verification_error(exc) from exc
                cx = report.counterexamples[0] if report.counterexamples else None

            trail.append(
                AttemptRecord(
                    attempt=attempt,
                    parity_score_pct=report.parity_score_pct,
                    passed_cases=report.passed_cases,
                    total_cases=report.total_cases,
                    counterexample=None if report.verified else cx,
                )
            )
            if report.verified and not float_attempt:
                return trail, FinalReport(report, True, "passed"), code
            if retries >= self.MAX_RETRIES or (not report.verified and cx is None):
                return trail, FinalReport(report, False, "failed"), code

            retries += 1
            if report.verified:  # the float demo matched: still need exact Decimal code
                code = exact_fn() if exact_fn is not None else \
                    self._llm.generate_python(cobol_source, program).verified_python_code
            elif repair_fn is not None:
                code = repair_fn(cx, code)  # type: ignore[arg-type]
            else:
                code = self._llm.repair_python(cobol_source, program, cx, code).verified_python_code
            float_attempt = False


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def _ms(start: float) -> int:  # float-ok: timing
    return max(0, int((time.perf_counter() - start) * 1000))


def _program_to_dict(program: InferredProgram) -> dict[str, Any]:
    return {
        "program_title": program.program_title,
        "description": program.description,
        "io_protocol": program.io_protocol,
        "contract": program.contract,
        "business_rules": list(program.business_rules),
        "dashboard_metadata": program.dashboard_metadata,
    }


def _program_from_dict(data: Mapping[str, Any]) -> InferredProgram:
    return InferredProgram(
        program_title=str(data["program_title"]),
        description=str(data["description"]),
        io_protocol=str(data["io_protocol"]),
        contract=data["contract"],
        business_rules=[str(r) for r in data["business_rules"]],
        dashboard_metadata=dict(data.get("dashboard_metadata") or {}),
    )


def _check_contract(program: InferredProgram) -> None:
    """Requirements 5.9 and 5.10."""
    inputs, outputs = program.contract.get("inputs", []), program.contract.get("outputs", [])
    missing = []
    if not outputs:
        missing.append("outputs")
    if program.io_protocol != "no_input_static" and not inputs:
        missing.append("inputs")
    if missing:
        raise PipelineStageError(
            422,
            "legacy_parser",
            {
                "error": "incomplete_contract",
                "message": f"The inferred contract has no {' and no '.join(missing)}.",
            },
        )


class Pipeline:
    """Legacy_Parser -> Cloud_Architect -> Parity_Auditor orchestration."""

    def __init__(self, llm: Any, cache: PipelineCache, runner: CobolRunner | None = None):
        self._llm = llm
        self._cache = cache
        self._runner = runner

    def run(
        self,
        cobol_code: str,
        *,
        num_cases: int = 150,
        demonstrate_self_healing: bool = False,
        force_live_llm: bool = False,
        preset_id: str | None = None,
    ) -> ResurrectResponse:
        """Run any COBOL source through the live pipeline.

        ``preset_id`` is only echoed back; the caller supplies the raw text.
        """
        verdict = validate_source(cobol_code)
        if not verdict.accepted:
            err = verdict.error
            assert err is not None
            status = 413 if err.token == "SOURCE-SIZE" else 400
            raise PipelineStageError(
                status, None,
                {"error": "rejected", "token": err.token, "line": err.line, "message": err.message},
            )
        runner = self._runner or _shared_runner()
        compiled = runner.get_binary(cobol_code)  # compile once; verification reuses the binary
        if not compiled.success:
            raise PipelineStageError(
                400, None,
                {"error": "compile_failed", "message": (compiled.stderr or "compile failed").strip()[:2000]},
            )

        key = cache_key(cobol_code, demonstrate_self_healing)
        cached = None if force_live_llm else self._cache.get(key)
        loop = SelfHealingLoop(self._llm, runner)
        stages: list[StageRecord] = []
        llm_used = False
        float_code: str | None = None

        try:
            if cached is not None and "program" in cached.extra:
                program = _program_from_dict(cached.extra["program"])
                first_code = cached.verified_python_code
                float_code = cached.extra.get("float_demo_code")
                stages += [
                    StageRecord("legacy_parser", "ok", 0, "cache"),
                    StageRecord("cloud_architect", "ok", 0, "cache"),
                ]
            else:
                cached = None
                self._llm.require_key()
                llm_used = True
                t0 = time.perf_counter()
                program = self._llm.infer_contract(cobol_code)
                _check_contract(program)
                program = dataclasses.replace(program, contract=normalize_contract(program.contract))
                stages.append(StageRecord("legacy_parser", "ok", _ms(t0), "llm"))
                t0 = time.perf_counter()
                architect = self._llm.generate_python(
                    cobol_code, program, float_demo=demonstrate_self_healing
                )
                first_code = architect.verified_python_code
                float_code = first_code if demonstrate_self_healing else None
                stages.append(StageRecord("cloud_architect", "ok", _ms(t0), "llm"))

            t1 = time.perf_counter()
            if demonstrate_self_healing and cached is not None:
                if float_code is None:
                    raise PipelineStageError(
                        500, "cloud_architect", {"error": "cache_incomplete", "message": "cached demo lacks float code"}
                    )
                exact = first_code
                trail, final, code = loop.run(
                    cobol_code, program, float_code, num_cases,
                    first_attempt_is_float_demo=True,
                    repair_fn=lambda _cx, _c: exact,
                    exact_fn=lambda: exact,
                )
            else:
                trail, final, code = loop.run(
                    cobol_code, program, first_code, num_cases,
                    first_attempt_is_float_demo=demonstrate_self_healing and cached is None,
                )
        except LLMUnavailable as exc:
            raise _llm_unavailable_error(exc, "pipeline") from None
        except LLMCallError as exc:
            raise _llm_call_error(exc) from None

        stages.append(StageRecord("parity_auditor", "ok" if final.certified else "failed", _ms(t1), "live"))

        schema = schema_from_contract(program.contract)
        dashboard = build_dashboard_metadata(program)
        pic_mappings = build_pic_mappings(schema)
        try:
            export_code = render(code, schema)
        except ValueError:
            export_code = ""  # uncertified or unusable code has no export

        if final.certified and cached is None and llm_used:
            self._cache.put(
                key,
                CachedResult(
                    business_rules=list(program.business_rules),
                    pic_mappings=pic_mappings,
                    dashboard_metadata=dashboard,
                    verified_python_code=code,
                    export_fastapi_code=export_code,
                    stages=[StageRecord(s.name, "ok", 0, "cache").to_dict() for s in stages[:2]],
                    extra={"program": _program_to_dict(program), "float_demo_code": float_code},
                ),
            )

        return ResurrectResponse(
            cobol_code=cobol_code,
            program_title=program.program_title,
            description=program.description,
            io_protocol=program.io_protocol,
            contract=program.contract,
            cache_hit=cached is not None,
            llm_used=llm_used,
            stages=stages,
            business_rules=list(program.business_rules),
            pic_mappings=pic_mappings,
            dashboard_metadata=dashboard,
            verified_python_code=code,
            export_fastapi_code=export_code,
            audit_trail=trail,
            final_report=final,
            preset_id=preset_id,
        )
