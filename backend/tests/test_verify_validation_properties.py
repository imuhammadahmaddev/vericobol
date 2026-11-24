"""Property 18: /api/verify request validation matrix is a pure function of request shape.

All shapes below are rejected before any compile or COBOL run, so the tests are
cheap and need no ``cobc``.
"""

# Feature: cobol-verification-engine, Property 18: /api/verify request validation matrix is a pure function of request shape

from __future__ import annotations

import json
import os

os.environ["VERICOBOL_SKIP_WARMUP"] = "1"

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings, strategies as st

from app.main import PRESET_IDS, app
from app.pbt_verifier import _fields

PBT = settings(
    max_examples=100,
    database=None,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)

_FLOAT_MARK = "__FLOAT_LITERAL__"

client = TestClient(app)


def _post(body: dict) -> int:
    text = json.dumps(body).replace(f'"{_FLOAT_MARK}"', "1.5")
    resp = client.post(
        "/api/verify", content=text, headers={"Content-Type": "application/json"}
    )
    return resp.status_code


def _schema(layout: str, money=("0", "100", "1")) -> list[dict]:
    lo, hi, step = money
    return [
        {"name": f.name, "min": lo, "max": hi, "step": step} for f in _fields(layout)
    ]


layouts = st.sampled_from(PRESET_IDS)
safe_text = st.text(
    alphabet=st.characters(min_codepoint=65, max_codepoint=90), min_size=0, max_size=30
)
safe_cobol = safe_text.map(lambda s: f"       STOP RUN. {s}")
prohibited_cobol = st.sampled_from(
    [
        '       CALL "SYSTEM" USING "ls".',
        "       OPEN OUTPUT OUT-FILE.",
        "       DISPLAY X UPON ENVIRONMENT-NAME.",
        "       ACCEPT X FROM ENVIRONMENT Y.",
    ]
).flatmap(lambda bad: safe_text.map(lambda s: f"       STOP RUN. {s}\n{bad}\n"))
py_code = safe_text.map(lambda s: f"# {s}\n")
valid_num_cases = st.one_of(st.none(), st.integers(1, 3))
money_ints = st.integers(0, 10**6)
money_strs = money_ints.map(str)

bad_num_cases = st.one_of(
    st.integers(max_value=0),
    st.integers(min_value=251),
    st.just(_FLOAT_MARK),  # 1.5, a JSON float literal
    st.text(min_size=0, max_size=5),
    st.booleans(),
    st.lists(st.integers(), max_size=2),
)


def _with_cases(body: dict, num_cases) -> dict:
    if num_cases is not None:
        body["num_cases"] = num_cases
    return body


@PBT
@given(
    cobol=safe_cobol, preset=st.one_of(layouts, st.just("zzz_unknown")), py=st.one_of(st.none(), py_code),
    nc=valid_num_cases,
)
def test_both_preset_and_cobol_is_422(cobol, preset, py, nc):
    body = _with_cases({"preset_id": preset, "cobol_code": cobol}, nc)
    if py is not None:
        body["python_code"] = py
    assert _post(body) == 422


@PBT
@given(py=st.one_of(st.none(), py_code), layout=layouts, nc=valid_num_cases, with_schema=st.booleans())
def test_neither_preset_nor_cobol_is_422(py, layout, nc, with_schema):
    body = _with_cases({}, nc)
    if py is not None:
        body["python_code"] = py
    if with_schema:
        body["field_schema"] = _schema(layout)
    assert _post(body) == 422


@PBT
@given(name=st.from_regex(r"[a-z][a-z0-9_]{0,20}", fullmatch=True), nc=valid_num_cases)
def test_unknown_preset_is_404(name, nc):
    if name in PRESET_IDS:
        return
    assert _post(_with_cases({"preset_id": name}, nc)) == 404


@PBT
@given(preset=layouts, nc=bad_num_cases)
def test_num_cases_out_of_range_or_non_integer_is_422(preset, nc):
    assert _post({"preset_id": preset, "num_cases": nc}) == 422


@PBT
@given(preset=st.one_of(layouts, st.just("zzz_unknown")), nc=st.sampled_from([0, 251, -5, 10**6]))
def test_num_cases_checked_before_preset_lookup(preset, nc):
    # Shape validation precedes the 404 lookup, so an unknown preset still yields 422.
    assert _post({"preset_id": preset, "num_cases": nc}) == 422


@PBT
@given(
    cobol=st.one_of(safe_cobol, prohibited_cobol),
    py=st.one_of(st.none(), py_code),
    schema_kind=st.sampled_from(["missing", "null", "empty"]),
    layout=layouts,
    nc=valid_num_cases,
)
def test_custom_cobol_missing_python_or_schema_is_422(cobol, py, schema_kind, layout, nc):
    # Missing python_code with a valid schema, or missing schema with any python_code.
    body = _with_cases({"cobol_code": cobol}, nc)
    if py is None:
        body["field_schema"] = _schema(layout)
    else:
        body["python_code"] = py
        if schema_kind == "null":
            body["field_schema"] = None
        elif schema_kind == "empty":
            body["field_schema"] = []
    assert _post(body) == 422


@PBT
@given(cobol=prohibited_cobol, py=py_code, layout=layouts, hi=money_strs, nc=valid_num_cases)
def test_prohibited_construct_is_400(cobol, py, layout, hi, nc):
    body = _with_cases(
        {
            "cobol_code": cobol,
            "python_code": py,
            "field_schema": _schema(layout, ("0", str(int(hi) + 1), "1")),
        },
        nc,
    )
    assert _post(body) == 400


@PBT
@given(cobol=st.one_of(safe_cobol, prohibited_cobol), py=py_code, layout=layouts, int_form=st.booleans(),
       which=st.sampled_from(["min", "max", "step"]), nc=valid_num_cases)
def test_float_typed_money_value_is_422(cobol, py, layout, int_form, which, nc):
    schema = _schema(layout)
    schema[0][which] = _FLOAT_MARK
    body = _with_cases({"cobol_code": cobol, "python_code": py, "field_schema": schema}, nc)
    assert _post(body) == 422


@PBT
@given(
    cobol=safe_cobol,
    py=py_code,
    layout=layouts,
    money=st.tuples(money_ints, money_ints, st.integers(1, 100)).map(
        lambda t: (str(min(t[0], t[1])), str(max(t[0], t[1])), str(t[2]))
    ),
    as_int=st.booleans(),
    nc=valid_num_cases,
)
def test_integer_and_string_money_values_pass_shape_checks(cobol, py, layout, money, as_int, nc):
    # Integers and strings are valid Money_Values; with schema names that match no
    # layout the request reaches the layout check and is a 422, not a type error 500.
    lo, hi, step = (int(x) if as_int else x for x in money)
    schema = [{"name": "not_a_field", "min": lo, "max": hi, "step": step}]
    body = _with_cases({"cobol_code": cobol, "python_code": py, "field_schema": schema}, nc)
    assert _post(body) == 422


@PBT
@given(cobol=safe_cobol, py=py_code, layout=layouts, extra=safe_text.filter(bool), nc=valid_num_cases)
def test_schema_names_must_match_a_preset_layout(cobol, py, layout, extra, nc):
    schema = _schema(layout)
    schema[0]["name"] = "x_" + extra.lower()
    body = _with_cases({"cobol_code": cobol, "python_code": py, "field_schema": schema}, nc)
    assert _post(body) == 422


@PBT
@given(
    shape=st.sampled_from(["neither", "both", "unknown", "bad_nc", "no_py", "prohibited", "float"]),
    data=st.data(),
)
def test_status_depends_only_on_shape(shape, data):
    expected = {
        "neither": 422, "both": 422, "unknown": 404, "bad_nc": 422,
        "no_py": 422, "prohibited": 400, "float": 422,
    }[shape]
    statuses = set()
    for _ in range(2):
        layout = data.draw(layouts)
        py = data.draw(py_code)
        if shape == "neither":
            body = {"python_code": py}
        elif shape == "both":
            body = {"preset_id": layout, "cobol_code": data.draw(safe_cobol)}
        elif shape == "unknown":
            body = {"preset_id": "q" + data.draw(st.from_regex(r"[a-z]{3,8}", fullmatch=True))}
        elif shape == "bad_nc":
            body = {"preset_id": layout, "num_cases": data.draw(bad_num_cases)}
        elif shape == "no_py":
            body = {"cobol_code": data.draw(safe_cobol), "field_schema": _schema(layout)}
        elif shape == "prohibited":
            body = {"cobol_code": data.draw(prohibited_cobol), "python_code": py,
                    "field_schema": _schema(layout, ("0", str(data.draw(money_ints) + 1), "1"))}
        else:
            schema = _schema(layout)
            schema[0]["min"] = _FLOAT_MARK
            body = {"cobol_code": data.draw(safe_cobol), "python_code": py, "field_schema": schema}
        statuses.add(_post(body))
    assert statuses == {expected}


def test_valid_preset_request_is_accepted():
    # One cheap end-to-end example: a valid shape is not rejected by validation.
    resp = client.post("/api/verify", json={"preset_id": "loan_calc", "num_cases": 1})
    assert resp.status_code not in (400, 404, 422)
