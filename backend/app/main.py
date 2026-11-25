"""VeriCOBOL FastAPI application: CORS, request size limit, /health, presets, resurrect."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, model_validator

from .catalog import DecimalJSONResponse, load_presets
from .cobol_runner import validate_source
from .export_template import render as render_export
from .llm_client import LLMCallError, LLMClient, LLMUnavailable
from .pbt_verifier import (
    Case,
    VerificationError,
    _check_run,
    _compare_case,
    _contract_fields,
    _fmt,
    _shared_runner,
    _to_value,
    parse_outputs,
    render_stdin,
    run_universal_verification,
    run_user_python,
)
from .pipeline_cache import PipelineCache
from .resurrection import (
    Pipeline,
    PipelineStageError,
    normalize_contract,
    schema_from_contract,
)

logger = logging.getLogger("vericobol")

MAX_BODY_BYTES = 128 * 1024
RESURRECT_MAX_BODY_BYTES = 256 * 1024
DEFAULT_FRONTEND_URL = "http://localhost:3000"
DEFAULT_NUM_CASES = 150
RESURRECT_MIN_CASES = 150
RESURRECT_MAX_CASES = 200


def _frontend_origin() -> str:
    value = (os.environ.get("FRONTEND_URL") or DEFAULT_FRONTEND_URL).strip()
    return value.rstrip("/") or DEFAULT_FRONTEND_URL


@lru_cache(maxsize=1)
def _cobc_info() -> tuple[bool, str]:
    """Run ``cobc --version`` once per process. Never raises."""
    cobc = os.environ.get("COBC_PATH") or "cobc"
    try:
        proc = subprocess.run(
            [cobc, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False, ""
    if proc.returncode != 0:
        return False, ""
    lines = (proc.stdout or "").strip().splitlines()
    return True, lines[0].strip() if lines else ""


class BodySizeLimitMiddleware:
    """Reject requests whose Content-Length exceeds the limit before reading the body."""

    def __init__(self, app, max_bytes: int = MAX_BODY_BYTES):
        self.app = app
        self.max_bytes = max_bytes
        big = max(max_bytes, RESURRECT_MAX_BODY_BYTES)
        self.path_limits = {
            "/api/resurrect": big,
            "/api/calculate-custom": big,
            "/api/export-custom": big,
        }

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            limit = self.path_limits.get(scope.get("path", ""), self.max_bytes)
            for name, value in scope.get("headers", []):
                if name == b"content-length":
                    try:
                        length = int(value)
                    except ValueError:
                        await self._reject(send, 400, "Invalid Content-Length header")
                        return
                    if length > limit:
                        await self._reject(
                            send, 413, f"Request body exceeds {limit} bytes"
                        )
                        return
                    break
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send, status: int, detail: str) -> None:
        body = ('{"detail":"%s"}' % detail).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


app = FastAPI(title="VeriCOBOL")
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_frontend_origin()],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


def _reject_constant(name: str):
    raise ValueError(f"JSON constant {name} is not allowed")


def _error(status: int, detail: Any) -> DecimalJSONResponse:
    return DecimalJSONResponse({"detail": detail}, status_code=status)


_VERIFY_LOCK = threading.Lock()  # one verification at a time: RAM and 0.1 CPU budget


# ---------------------------------------------------------------------------
# Health and presets
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    """Cheap liveness probe: no LLM call, no compile. ``cobc --version`` is cached."""
    available, version = _cobc_info()
    return {
        "status": "ok",
        "cobc_available": available,
        "cobc_version": version,
        "llm_configured": LLMClient.from_env().config is not None,
    }


def _preset_sources() -> dict[str, str]:
    return {p["preset_id"]: p["cobol_source"] for p in load_presets()}


@app.get("/api/presets", response_class=DecimalJSONResponse)
def list_presets() -> DecimalJSONResponse:
    return DecimalJSONResponse(
        [
            {"id": p["preset_id"], "title": p["name"], "cobol_code": p["cobol_source"]}
            for p in load_presets()
        ]
    )


# ---------------------------------------------------------------------------
# Resurrection pipeline endpoint
# ---------------------------------------------------------------------------

class ResurrectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset_id: str | None = None
    cobol_code: str | None = None
    num_cases: int = Field(
        default=DEFAULT_NUM_CASES, ge=RESURRECT_MIN_CASES, le=RESURRECT_MAX_CASES, strict=True
    )
    demonstrate_self_healing: bool = False
    force_live_llm: bool = False

    @model_validator(mode="after")
    def _shape(self) -> "ResurrectRequest":
        if (self.preset_id is None) == (self.cobol_code is None):
            raise ValueError("supply exactly one of preset_id or cobol_code")
        return self


_PIPELINE: Pipeline | None = None
_PIPELINE_LOCK = threading.Lock()


def _get_pipeline() -> Pipeline:
    """Build the process-wide Pipeline on first use (never on /health)."""
    global _PIPELINE
    with _PIPELINE_LOCK:
        if _PIPELINE is None:
            _PIPELINE = Pipeline(LLMClient.from_env(), PipelineCache())
        return _PIPELINE


def _resurrect_blocking(req: ResurrectRequest, cobol_code: str) -> DecimalJSONResponse:
    try:
        with _VERIFY_LOCK:
            response = _get_pipeline().run(
                cobol_code,
                num_cases=req.num_cases,
                demonstrate_self_healing=req.demonstrate_self_healing,
                force_live_llm=req.force_live_llm,
                preset_id=req.preset_id,
            )
    except PipelineStageError as exc:
        return _error(exc.status, exc.body)
    except LLMUnavailable as exc:
        return _error(
            503,
            {"error": "llm_unavailable", "cause": exc.cause,
             "message": f"The LLM is unavailable: {exc}."},
        )
    except LLMCallError as exc:
        return _error(
            502,
            {"error": "llm_call_failed", "stage": exc.stage,
             "message": f"The LLM call failed in stage {exc.stage}."},
        )
    return DecimalJSONResponse(response.to_dict())


@app.post("/api/resurrect", response_class=DecimalJSONResponse)
async def resurrect(request: Request) -> DecimalJSONResponse:
    body = await request.body()
    if len(body) > RESURRECT_MAX_BODY_BYTES:  # chunked uploads carry no Content-Length
        return _error(413, f"Request body exceeds {RESURRECT_MAX_BODY_BYTES} bytes")
    try:
        data = json.loads(body, parse_float=Decimal, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        return _error(422, "request body is not valid JSON")
    try:
        req = ResurrectRequest.model_validate(data)
    except ValidationError as exc:
        return _error(
            422,
            [
                {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]}
                for e in exc.errors(include_url=False, include_context=False, include_input=False)
            ],
        )

    if req.preset_id is not None:
        sources = _preset_sources()
        if req.preset_id not in sources:
            return _error(404, {"error": "unknown_preset", "valid_ids": list(sources)})
        cobol_code = sources[req.preset_id]
    else:
        cobol_code = req.cobol_code or ""
    return await run_in_threadpool(_resurrect_blocking, req, cobol_code)


# ---------------------------------------------------------------------------
# Custom-program endpoints: calculate-custom and export-custom
# ---------------------------------------------------------------------------

_IO_PROTOCOLS = ("batch_space", "single_run_lines", "single_run_space", "no_input_static")


class CalculateCustomRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cobol_code: StrictStr
    python_code: StrictStr
    schema_: dict[str, Any] = Field(alias="schema")
    io_protocol: Literal["batch_space", "single_run_lines", "single_run_space", "no_input_static"]
    inputs: dict[StrictStr, StrictStr]


class ExportCustomRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    program_title: StrictStr | None = None
    verified_python_code: StrictStr | None = None
    python_code: StrictStr | None = None  # alias used by the frontend body
    schema_: dict[str, Any] = Field(alias="schema")
    cobol_code: StrictStr | None = None   # when given, certification is re-proved live
    io_protocol: Literal[
        "batch_space", "single_run_lines", "single_run_space", "no_input_static"
    ] | None = None

    @model_validator(mode="after")
    def _code(self) -> "ExportCustomRequest":
        if (self.verified_python_code or self.python_code) is None:
            raise ValueError("supply verified_python_code")
        return self


async def _parse_body(request: Request, model: type[BaseModel]) -> BaseModel | DecimalJSONResponse:
    body = await request.body()
    if len(body) > RESURRECT_MAX_BODY_BYTES:
        return _error(413, f"Request body exceeds {RESURRECT_MAX_BODY_BYTES} bytes")
    try:
        data = json.loads(body, parse_float=Decimal, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        return _error(422, "request body is not valid JSON")
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        return _error(
            422,
            [
                {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]}
                for e in exc.errors(include_url=False, include_context=False, include_input=False)
            ],
        )


def _source_rejection(cobol_code: str) -> DecimalJSONResponse | None:
    verdict = validate_source(cobol_code)
    if verdict.accepted:
        return None
    err = verdict.error
    assert err is not None
    return _error(
        413 if err.token == "SOURCE-SIZE" else 400,
        {"error": "rejected", "token": err.token, "line": err.line, "message": err.message},
    )


def _field_error(name: str, message: str) -> DecimalJSONResponse:
    return _error(422, {"error": "invalid_input", "field": name, "message": message})


def _calculate_blocking(req: CalculateCustomRequest) -> DecimalJSONResponse:
    schema = req.schema_
    in_defs = schema.get("inputs")
    out_defs = schema.get("outputs")
    if not isinstance(in_defs, list) or not isinstance(out_defs, list) or not out_defs:
        return _error(422, {"error": "invalid_schema",
                            "message": "schema needs an inputs list and a non-empty outputs list"})
    if req.io_protocol != "no_input_static" and not in_defs:
        return _error(422, {"error": "invalid_schema", "message": "schema has no inputs"})
    try:
        out_names = tuple(str(o["name"]) for o in out_defs)
        fields = _contract_fields(schema)
    except (KeyError, TypeError, ValueError):
        return _error(422, {"error": "invalid_schema", "message": "malformed schema fields"})

    known = {f.name for f in fields}
    for name in req.inputs:
        if name not in known:
            return _field_error(name, "unknown input field")

    units: dict[str, int] = {}
    for f in fields:
        raw = req.inputs.get(f.name)
        if raw is None:
            return _field_error(f.name, "missing input value")
        try:
            value = Decimal(raw.strip())
        except (InvalidOperation, ValueError):
            return _field_error(f.name, "must be a decimal string")
        if not value.is_finite():
            return _field_error(f.name, "must be a finite decimal")
        scaled = value.scaleb(f.scale)
        if scaled != scaled.to_integral_value():
            return _field_error(f.name, f"has more than {f.scale} decimal places")
        u = int(scaled)
        if not f.lo <= u <= f.hi:
            return _field_error(
                f.name,
                f"out of range {_fmt(_to_value(f.lo, f.scale))} to {_fmt(_to_value(f.hi, f.scale))}",
            )
        units[f.name] = u

    case = Case("custom", {f.name: _to_value(units[f.name], f.scale) for f in fields})
    runner = _shared_runner()
    with _VERIFY_LOCK:
        compiled = runner.get_binary(req.cobol_code)
        if not compiled.success or compiled.binary_path is None:
            return _error(400, {"error": "compile_failed",
                                "message": (compiled.stderr or "compile failed").strip()[:2000]})
        stdin_text = render_stdin(case, fields, req.io_protocol)
        if req.io_protocol == "batch_space":
            run = runner.run_batch(compiled.binary_path, [stdin_text])
        else:
            run = runner.run_single(compiled.binary_path, stdin_text)
        try:
            _check_run(run)
        except VerificationError as exc:
            return _error(502, {"error": exc.kind, "message": exc.message})
        py = run_user_python(req.python_code, [case])[0]

    if py.outputs is None:
        return _error(422, {"error": "python_error", "message": (py.error or "python failed")[:300]})

    tokens = parse_outputs(run.stdout, len(out_names), req.io_protocol)
    line = " ".join(tokens) if tokens is not None else ""
    inputs_text = {k: _fmt(v) for k, v in case.inputs.items()}
    mismatch = _compare_case(case, inputs_text, out_names, line, py.outputs, None, False)
    return DecimalJSONResponse(
        {
            "result": {n: _fmt(py.outputs[n]) for n in out_names if n in py.outputs},
            "raw_cobol_output": run.stdout,
            "parity_verified": mismatch is None,
        }
    )


@app.post("/api/calculate-custom", response_class=DecimalJSONResponse)
async def calculate_custom(request: Request) -> DecimalJSONResponse:
    req = await _parse_body(request, CalculateCustomRequest)
    if isinstance(req, DecimalJSONResponse):
        return req
    rejection = _source_rejection(req.cobol_code)
    if rejection is not None:
        return rejection
    return await run_in_threadpool(_calculate_blocking, req)


def _slug(title: str | None) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (title or "").lower()).strip("_")[:60]
    return slug or "verified_program"


def _export_blocking(req: ExportCustomRequest) -> DecimalJSONResponse:
    code = req.verified_python_code or req.python_code or ""
    try:
        contract = normalize_contract(req.schema_)
        schema = schema_from_contract(contract)
        module = render_export(code, schema)
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        return _error(422, {"error": "invalid_export", "message": str(exc)[:300]})

    certified = False
    if req.cobol_code is not None and req.io_protocol is not None:
        try:
            with _VERIFY_LOCK:
                report = run_universal_verification(
                    req.cobol_code, contract, req.io_protocol, code, runner=_shared_runner()
                )
            certified = report.verified
        except VerificationError:
            certified = False
    return DecimalJSONResponse(
        {
            "filename": f"{_slug(req.program_title)}_api.py",
            "verified_python_code": code,
            "export_fastapi_code": module,
            "certified": certified,
        }
    )


@app.post("/api/export-custom", response_class=DecimalJSONResponse)
async def export_custom(request: Request) -> DecimalJSONResponse:
    req = await _parse_body(request, ExportCustomRequest)
    if isinstance(req, DecimalJSONResponse):
        return req
    if req.cobol_code is not None:
        rejection = _source_rejection(req.cobol_code)
        if rejection is not None:
            return rejection
    return await run_in_threadpool(_export_blocking, req)
