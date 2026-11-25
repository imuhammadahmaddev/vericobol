"""VeriCOBOL GnuCOBOL MCP server.

Newline-delimited JSON-RPC 2.0 over stdio. Only JSON-RPC messages are written
to stdout; diagnostics go to stderr. Run from the backend directory (the image
WORKDIR) with ``python -m mcp_server.server``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

try:  # normal case: run from the backend directory / image WORKDIR
    import app  # noqa: F401
except ImportError:  # run from the repository root: add backend/ to sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import catalog, pbt_verifier  # noqa: E402
from app.cobol_runner import CobolRunner, validate_source  # noqa: E402

SERVER_NAME = "vericobol-gnucobol"
SERVER_VERSION = "1.0.0"
DEFAULT_PROTOCOL_VERSION = "2024-11-05"
MAX_REQUEST_ROWS = 1000

TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_cobol_presets",
        "description": "List the bundled COBOL presets with their input and output field names.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "compile_and_run_cobol",
        "description": (
            "Validate COBOL source against the sandbox rules, compile it with GnuCOBOL in a "
            "fresh temporary directory and run it with one stdin line per input row."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "cobol_source": {"type": "string", "description": "Free-format COBOL source."},
                "input_rows": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Lines written to the program's stdin.",
                },
            },
            "required": ["cobol_source"],
            "additionalProperties": False,
        },
    },
    {
        "name": "verify_cobol_parity",
        "description": (
            "Run differential property-based verification of a preset (or custom COBOL with a "
            "field_schema) against the Decimal Python implementation; returns the ParityReport."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "preset_id": {
                    "type": "string",
                    "enum": ["loan_calc", "compound_int", "daily_ledger"],
                },
                "cobol_code": {"type": "string", "description": "Custom COBOL source."},
                "field_schema": {
                    "type": "array",
                    "description": "Custom COBOL contract: objects with name and direction.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "pic": {"type": "string"},
                            "direction": {"type": "string", "enum": ["input", "output"]},
                        },
                        "required": ["name", "direction"],
                    },
                },
                "python_code": {
                    "type": "string",
                    "description": "Decimal Python `calculate(**inputs)` to verify against the COBOL.",
                },
                "num_cases": {"type": "integer", "minimum": 150, "maximum": 200},
            },
            "additionalProperties": False,
        },
    },
]


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def write(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, separators=(",", ":"), ensure_ascii=True) + "\n")
    sys.stdout.flush()


def error_response(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _result(payload: Any, is_error: bool = False) -> dict[str, Any]:
    text = json.dumps(payload, separators=(",", ":"), default=str)
    out: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        out["isError"] = True
    return out


def _error_result(message: str, **extra: Any) -> dict[str, Any]:
    return _result({"error": message, **extra}, is_error=True)


# -- tools --------------------------------------------------------------------

def _output_names(preset_id: str) -> list[str]:
    return list(pbt_verifier._OUTPUT_FIELDS.get(preset_id, ()))  # noqa: SLF001


def tool_list_presets(_args: dict[str, Any]) -> dict[str, Any]:
    presets = []
    for p in catalog.load_presets():
        presets.append(
            {
                "preset_id": p["preset_id"],
                "title": p["name"],
                "input_fields": [f["name"] for f in p["input_schema"]],
                "output_fields": _output_names(p["preset_id"]),
            }
        )
    return _result({"presets": presets})


def tool_compile_and_run(args: dict[str, Any]) -> dict[str, Any]:
    source = args.get("cobol_source")
    rows = args.get("input_rows", [])
    if not isinstance(source, str) or not source.strip():
        return _error_result("cobol_source must be a non-empty string")
    if not isinstance(rows, list) or not all(isinstance(r, str) for r in rows):
        return _error_result("input_rows must be an array of strings")
    if len(rows) > MAX_REQUEST_ROWS:
        return _error_result(f"input_rows exceeds the {MAX_REQUEST_ROWS} row limit")
    verdict = validate_source(source)
    if not verdict.accepted:
        err = verdict.error
        return _error_result(err.message if err else "Source rejected", rejected=True)
    # compile_and_run_custom compiles and runs inside a fresh TemporaryDirectory.
    compiled, run = CobolRunner().compile_and_run_custom(source, rows)
    if not compiled.success or run is None:
        return _error_result(
            "compile failed",
            stdout=compiled.stdout,
            stderr=compiled.stderr,
            exit_code=None,
            timed_out=compiled.timed_out,
        )
    return _result(
        {
            "stdout": run.stdout,
            "stderr": run.stderr,
            "exit_code": run.exit_code,
            "truncated": run.truncated,
            "timed_out": run.timed_out,
        }
    )


def _preset_from_schema(schema: Any) -> str | None:
    """Pick the preset whose input field names match the schema's input fields."""
    if not isinstance(schema, list):
        return None
    names = [f.get("name") for f in schema if isinstance(f, dict) and f.get("direction") == "input"]
    for p in catalog.load_presets():
        if names == [f["name"] for f in p["input_schema"]]:
            return p["preset_id"]
    return None


def tool_verify_parity(args: dict[str, Any]) -> dict[str, Any]:
    preset_id = args.get("preset_id")
    cobol_code = args.get("cobol_code")
    num_cases = args.get("num_cases", pbt_verifier.MIN_CASES)
    if isinstance(num_cases, bool) or not isinstance(num_cases, int):
        return _error_result("num_cases must be an integer")
    if cobol_code is not None:
        if not isinstance(cobol_code, str):
            return _error_result("cobol_code must be a string")
        verdict = validate_source(cobol_code)
        if not verdict.accepted:
            err = verdict.error
            return _error_result(err.message if err else "Source rejected", rejected=True)
        if preset_id is None:
            preset_id = _preset_from_schema(args.get("field_schema"))
            if preset_id is None:
                return _error_result(
                    "cobol_code requires preset_id or a field_schema whose input fields "
                    "match a preset contract"
                )
    elif preset_id is None:
        return _error_result("preset_id or cobol_code is required")
    python_code = args.get("python_code")
    if not isinstance(python_code, str) or not python_code.strip():
        return _error_result("python_code is required")
    try:
        report = pbt_verifier.run_verification(
            preset_id, cobol_code, num_cases=num_cases, python_code=python_code
        )
    except pbt_verifier.VerificationError as exc:
        return _error_result(exc.message, kind=exc.kind, rejected=exc.kind == "rejected")
    return _result(report.to_dict())


TOOL_HANDLERS = {
    "list_cobol_presets": tool_list_presets,
    "compile_and_run_cobol": tool_compile_and_run,
    "verify_cobol_parity": tool_verify_parity,
}


# -- dispatch -----------------------------------------------------------------

def handle(msg: Any) -> dict[str, Any] | None:
    """Return the response for one decoded message, or None for no response."""
    if not isinstance(msg, dict) or not isinstance(msg.get("method"), str):
        req_id = msg.get("id") if isinstance(msg, dict) else None
        return error_response(req_id, -32600, "Invalid Request")
    method = msg["method"]
    is_notification = "id" not in msg
    req_id = msg.get("id")
    params = msg.get("params")
    if not isinstance(params, dict):
        params = {}

    if is_notification:
        # notifications/initialized and other notifications never get a reply
        if method not in ("notifications/initialized",):
            log(f"ignored notification: {method}")
        return None

    if method == "initialize":
        version = params.get("protocolVersion")
        if not isinstance(version, str):
            version = DEFAULT_PROTOCOL_VERSION
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = params.get("name")
        handler = TOOL_HANDLERS.get(name) if isinstance(name, str) else None
        if handler is None:
            return error_response(req_id, -32602, f"Unknown tool: {name}")
        arguments = params.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        try:
            result = handler(arguments)
        except Exception as exc:  # noqa: BLE001 - never crash the loop
            log(f"tool {name} failed: {type(exc).__name__}: {exc}")
            result = _error_result(f"internal error: {type(exc).__name__}")
        return {"jsonrpc": "2.0", "id": req_id, "result": result}
    return error_response(req_id, -32601, f"Method not found: {method}")


def main() -> None:
    log(f"{SERVER_NAME} MCP server ready")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            write(error_response(None, -32700, "Parse error"))
            continue
        response = handle(msg)
        if response is not None:
            write(response)


if __name__ == "__main__":
    main()
