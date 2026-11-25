"""Render a verified ``calculate`` function into a self-contained FastAPI module.

Pure string templating: no LLM, no exec. The verified code is embedded verbatim.
The generated module exposes one ``POST /calculate`` route. Every input and
output is a JSON string holding a Decimal, so no binary number type is involved.

The module namespace mirrors the verification sandbox: ``Decimal``,
``ROUND_HALF_UP``, ``ROUND_DOWN`` and a ``Context`` instance (precision 60,
ROUND_HALF_UP) are defined before the embedded code runs.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .resurrection import ContractSchema

_ENTRY_NAME = "calculate"

_PREAMBLE = '''\
"""Verified COBOL resurrection: exact-Decimal calculation exposed as a FastAPI service.

Run with:  uvicorn <module_name>:app
All request and response values are strings holding Decimal numbers.
"""
import decimal as _decimal
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

Context = _decimal.Context(prec=60, rounding=ROUND_HALF_UP)

# ---- verified code (embedded verbatim) ----
'''

_ROUTE = '''

# ---- end of verified code ----
_vc_entry = {entry}

from fastapi import Body as _vc_Body, FastAPI as _vc_FastAPI, HTTPException as _vc_HTTPException

# (name, scale) in contract order. Scale 0 means the function receives an int.
_VC_INPUTS = {inputs}
_VC_OUTPUTS = {outputs}

app = _vc_FastAPI(title="Verified COBOL calculation")


def _vc_parse(name, scale, text):
    try:
        value = Decimal(text.strip())
    except (_decimal.InvalidOperation, ValueError):
        raise _vc_HTTPException(status_code=422, detail=name + " must be a decimal string")
    if not value.is_finite():
        raise _vc_HTTPException(status_code=422, detail=name + " must be a finite decimal")
    if scale == 0:
        if value != value.to_integral_value():
            raise _vc_HTTPException(status_code=422, detail=name + " must be a whole number")
        return int(value)
    return value


@app.post("/calculate")
def _vc_calculate(payload: dict[str, str] = _vc_Body(...)) -> dict[str, str]:
    expected = [name for name, _ in _VC_INPUTS]
    if sorted(payload) != sorted(expected):
        raise _vc_HTTPException(
            status_code=422, detail="expected exactly these string fields: " + ", ".join(expected)
        )
    kwargs = {{name: _vc_parse(name, scale, payload[name]) for name, scale in _VC_INPUTS}}
    raw = _vc_entry(**kwargs)
    result = {{}}
    for key, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, (Decimal, int)):
            raise _vc_HTTPException(status_code=500, detail="calculation returned a non-Decimal value")
        result[key] = str(Decimal(value))
    missing = [name for name in _VC_OUTPUTS if name not in result]
    if missing:
        raise _vc_HTTPException(status_code=500, detail="calculation did not return: " + ", ".join(missing))
    return result
'''


def _entry_name(code: str) -> str:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise ValueError(f"verified_python_code is not valid Python: {exc}") from None
    funcs = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
    if _ENTRY_NAME in funcs:
        return _ENTRY_NAME
    if len(funcs) == 1:
        return funcs[0]
    raise ValueError(
        f"verified_python_code must define {_ENTRY_NAME!r} or exactly one top-level function"
    )


def render(verified_python_code: str, schema: "ContractSchema") -> str:
    """Return the source of a self-contained FastAPI module.

    ``verified_python_code`` is embedded verbatim. The caller guarantees it is
    free of binary floating point (the pipeline scans for this upstream).
    """
    entry = _entry_name(verified_python_code)
    inputs = tuple((f.name, f.scale) for f in schema.input_fields)
    outputs = tuple(f.name for f in schema.output_fields)
    code = verified_python_code if verified_python_code.endswith("\n") else verified_python_code + "\n"
    route = _ROUTE.format(entry=entry, inputs=repr(inputs), outputs=repr(outputs))
    return _PREAMBLE + code + route
