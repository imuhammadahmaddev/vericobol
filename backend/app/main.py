"""VeriCOBOL FastAPI application: CORS, request size limit, /health, warm-up."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Annotated, Any

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from .catalog import DecimalJSONResponse, load_presets
from .cobol_runner import validate_source
from .pbt_verifier import MAX_CASES, VerificationError, _catalog, _fields, run_verification

logger = logging.getLogger("vericobol")

MAX_BODY_BYTES = 128 * 1024
DEFAULT_FRONTEND_URL = "http://localhost:3000"
DEFAULT_NUM_CASES = 150
PRESET_IDS = ("loan_calc", "compound_int", "daily_ledger")


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

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            for name, value in scope.get("headers", []):
                if name == b"content-length":
                    try:
                        length = int(value)
                    except ValueError:
                        await self._reject(send, 400, "Invalid Content-Length header")
                        return
                    if length > self.max_bytes:
                        await self._reject(
                            send, 413, f"Request body exceeds {self.max_bytes} bytes"
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


def _warm_presets() -> None:
    """Compile the three presets once so the first request is not cold."""
    try:
        from .pbt_verifier import _shared_runner

        runner = _shared_runner()
        for preset_id in PRESET_IDS:
            runner.compile_and_run_preset(preset_id, [])
    except Exception:  # warm-up is best-effort and must never affect serving
        logger.warning("Preset warm-up failed", exc_info=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if os.environ.get("VERICOBOL_SKIP_WARMUP") != "1":
        threading.Thread(target=_warm_presets, name="preset-warmup", daemon=True).start()
    yield


app = FastAPI(title="VeriCOBOL", lifespan=lifespan)
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_frontend_origin()],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


class _JsonFloat(Decimal):
    """Marks a JSON number written with a fraction/exponent (a float literal)."""


def _reject_constant(name: str):
    raise ValueError(f"JSON constant {name} is not allowed")


def _money(value: Any) -> Decimal:
    """Money_Value: JSON string or integer. A float literal is rejected (Req 17.13)."""
    if isinstance(value, _JsonFloat):
        raise ValueError("float-typed Money_Value is not allowed; send a string or integer")
    if isinstance(value, bool):
        raise ValueError("Money_Value must be a string or integer")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            result = Decimal(value.strip())
        except InvalidOperation:
            raise ValueError("Money_Value is not a valid decimal number") from None
        if not result.is_finite():
            raise ValueError("Money_Value must be finite")
        return result
    raise ValueError("Money_Value must be a string or integer")


MoneyValue = Annotated[Decimal, BeforeValidator(_money)]


class FieldSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    min: MoneyValue
    max: MoneyValue
    step: MoneyValue

    @model_validator(mode="after")
    def _bounds(self) -> "FieldSpec":
        if self.min > self.max:
            raise ValueError("min must not exceed max")
        if self.step <= 0:
            raise ValueError("step must be positive")
        return self


class VerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset_id: str | None = None
    cobol_code: str | None = None
    python_code: str | None = None
    field_schema: list[FieldSpec] | None = Field(default=None, max_length=32)
    num_cases: int = Field(default=DEFAULT_NUM_CASES, ge=1, le=MAX_CASES, strict=True)
    simulate_float_bug: bool = False

    @model_validator(mode="after")
    def _shape(self) -> "VerifyRequest":
        if (self.preset_id is None) == (self.cobol_code is None):
            raise ValueError("supply exactly one of preset_id or cobol_code")
        if self.cobol_code is not None and (
            self.python_code is None or not self.field_schema
        ):
            raise ValueError("cobol_code requires python_code and field_schema")
        return self


def _error(status: int, detail: Any) -> DecimalJSONResponse:
    return DecimalJSONResponse({"detail": detail}, status_code=status)


def _layout_for_schema(schema: list[FieldSpec]) -> str | None:
    """Preset layout whose input names equal the schema's names, else None."""
    names = sorted(f.name for f in schema)
    for preset_id in PRESET_IDS:
        if names == sorted(f.name for f in _fields(preset_id)):
            return preset_id
    return None


_VERIFY_LOCK = threading.Lock()  # one verification at a time: RAM and 0.1 CPU budget


def _verify_blocking(req: VerifyRequest, layout: str) -> DecimalJSONResponse:
    try:
        with _VERIFY_LOCK:
            report = run_verification(
                layout,
                req.cobol_code,
                None,
                req.num_cases,
                python_code=req.python_code,
                simulate_float_bug=req.simulate_float_bug,
            )
    except VerificationError as exc:
        custom = req.cobol_code is not None
        if exc.kind == "rejected":
            return _error(400, {"error": "rejected", "message": exc.message})
        if exc.kind == "run_timeout":
            return _error(504, {"error": exc.kind, "message": exc.message})
        if exc.kind in ("compile_failed", "invalid_request"):
            return _error(422, {"error": exc.kind, "message": exc.message})
        # run_failed / output_truncated / row_count_mismatch: a pasted program that
        # breaks the Batch_Protocol is a 422; a bundled preset doing so is a server fault.
        return _error(422 if custom else 500, {"error": exc.kind, "message": exc.message})
    return DecimalJSONResponse(report.to_dict())


@app.post("/api/verify", response_class=DecimalJSONResponse)
async def verify(request: Request) -> DecimalJSONResponse:
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:  # chunked uploads carry no Content-Length
        return _error(413, f"Request body exceeds {MAX_BODY_BYTES} bytes")
    try:
        data = json.loads(body, parse_float=_JsonFloat, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        return _error(422, "request body is not valid JSON")
    try:
        req = VerifyRequest.model_validate(data)
    except ValidationError as exc:
        return _error(
            422,
            [
                {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]}
                for e in exc.errors(include_url=False, include_context=False, include_input=False)
            ],
        )

    if req.preset_id is not None:
        if req.preset_id not in PRESET_IDS:
            return _error(404, f"unknown preset_id: {req.preset_id[:64]!r}")
        layout = req.preset_id
    else:
        verdict = validate_source(req.cobol_code or "")
        if not verdict.accepted and verdict.error is not None:
            err = verdict.error
            return _error(
                400,
                {"error": "rejected", "token": err.token, "line": err.line,
                 "message": err.message},
            )
        matched = _layout_for_schema(req.field_schema or [])
        if matched is None:
            return _error(
                422,
                "field_schema names must match the inputs of a supported layout "
                "(loan_calc, compound_int or daily_ledger)",
            )
        layout = matched
    return await run_in_threadpool(_verify_blocking, req, layout)


_CALC_DEFAULT_THRESHOLD = Decimal("0.00")


def _calc_value(raw: Any, f: Any) -> Decimal | int:
    """Convert one request value to an exact Decimal/int for field ``f``.

    Raises ValueError with a short reason (the caller names the field).
    """
    if isinstance(raw, bool) or not isinstance(raw, (str, int, Decimal)):
        raise ValueError("must be a number or numeric string")
    try:
        value = Decimal(raw.strip()) if isinstance(raw, str) else Decimal(raw)
    except InvalidOperation:
        raise ValueError("is not a valid decimal number") from None
    if not value.is_finite():
        raise ValueError("must be finite")
    lo = Decimal(f.lo).scaleb(-f.scale)
    hi = Decimal(f.hi).scaleb(-f.scale)
    if value < lo or value > hi:
        raise ValueError(f"is out of range [{lo}, {hi}]")
    if value != value.quantize(Decimal(1).scaleb(-f.scale)):
        raise ValueError(f"must have at most {f.scale} decimal places")
    if f.scale == 0:
        return int(value)
    return value.quantize(Decimal(1).scaleb(-f.scale))


def _calculate_blocking(preset_id: str, inputs: dict[str, Decimal | int]) -> DecimalJSONResponse:
    from . import reference_models
    from .pbt_verifier import (
        _DELTA_CONTEXT,
        _OUTPUT_FIELDS,
        _ZERO_DELTA,
        Case,
        _format_row,
        _shared_runner,
    )

    fields = _fields(preset_id)
    row = _format_row(Case(preset_id, inputs), fields)
    out_names = _OUTPUT_FIELDS[preset_id]
    try:
        expected = reference_models.REGISTRY[preset_id](**inputs)
    except Exception as exc:  # noqa: BLE001
        return _error(500, {"error": "reference_model_failed",
                            "message": f"{type(exc).__name__}: {exc}"[:300]})
    run = _shared_runner().compile_and_run_preset(preset_id, [row])
    if run.timed_out:
        return _error(504, {"error": "run_timeout", "message": "COBOL run exceeded 5 seconds"})
    if run.truncated or run.exit_code != 0:
        return _error(500, {"error": "run_failed",
                            "message": f"COBOL exited with code {run.exit_code}"})
    lines = run.stdout.splitlines()
    tokens = lines[0].split() if len(lines) == 1 else []
    if len(tokens) != len(out_names):
        return _error(500, {"error": "row_count_mismatch", "field": out_names[0],
                            "message": "COBOL output did not match the Batch_Protocol"})
    result: dict[str, str] = {}
    for name, token in zip(out_names, tokens):
        try:
            cobol_val = Decimal(token)
            delta = abs(_DELTA_CONTEXT.subtract(cobol_val, Decimal(expected[name])))
        except (InvalidOperation, KeyError, TypeError):
            return _error(500, {"error": "parity_mismatch", "field": name,
                                "message": f"field {name} could not be compared"})
        if not delta == _ZERO_DELTA:
            return _error(500, {"error": "parity_mismatch", "field": name,
                                "message": f"COBOL and Reference_Model differ in {name}"})
        result[name] = format(Decimal(expected[name]), "f")
    return DecimalJSONResponse(
        {"preset_id": preset_id, "result": result, "raw_cobol_output": run.stdout}
    )


@app.post("/api/calculate", response_class=DecimalJSONResponse)
async def calculate(request: Request) -> DecimalJSONResponse:
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        return _error(413, f"Request body exceeds {MAX_BODY_BYTES} bytes")
    try:
        data = json.loads(body, parse_float=Decimal, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        return _error(422, "request body is not valid JSON")
    if not isinstance(data, dict):
        return _error(422, "request body must be a JSON object")
    preset_id = data.get("preset_id")
    if not isinstance(preset_id, str):
        return _error(422, {"field": "preset_id", "message": "preset_id is required"})
    if preset_id not in PRESET_IDS:
        return _error(404, f"unknown preset_id: {preset_id[:64]!r}")
    raw_inputs = data.get("inputs", data)
    if not isinstance(raw_inputs, dict):
        return _error(422, {"field": "inputs", "message": "inputs must be an object"})

    schema = _catalog()[preset_id]["input_schema"]
    fields = {f.name: f for f in _fields(preset_id)}
    inputs: dict[str, Decimal | int] = {}
    for spec in schema:
        name = spec["name"]
        if name in raw_inputs:
            raw = raw_inputs[name]
        elif "default" in spec:
            raw = spec["default"]
        else:
            return _error(422, {"field": name, "message": f"{name} is required"})
        try:
            inputs[name] = _calc_value(raw, fields[name])
        except ValueError as exc:
            return _error(422, {"field": name, "message": f"{name} {exc}"})
    return await run_in_threadpool(_calculate_blocking, preset_id, inputs)


@app.get("/api/presets", response_class=DecimalJSONResponse)
def list_presets() -> DecimalJSONResponse:
    return DecimalJSONResponse(load_presets())


@app.get("/health")
def health() -> dict:
    available, version = _cobc_info()
    return {"status": "ok", "cobc_available": available, "cobc_version": version}
