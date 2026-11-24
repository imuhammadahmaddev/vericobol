"""Property tests for POST /api/calculate.

# Feature: cobol-verification-engine, Property 19: /api/calculate validates
# fields and detects any COBOL/Reference_Model mismatch

**Validates: Requirements 18.3, 18.4, 18.5, 18.7, 18.8**

Runs real GnuCOBOL through the endpoint (Docker image only). Request bodies are
built as raw JSON text so numbers reach the server as JSON numbers, never as
Python floats.
"""

from __future__ import annotations

import json
import os

os.environ["VERICOBOL_SKIP_WARMUP"] = "1"

from decimal import Decimal
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app import reference_models
from app.main import app

PBT_SETTINGS = settings(
    max_examples=100,
    database=None,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)

# name -> (scale, min_units, max_units, has_default), from presets.json.
SCHEMA: dict[str, dict[str, tuple[int, int, int, bool]]] = {
    "loan_calc": {
        "principal": (2, 1, 1_000_000_000, False),
        "annual_rate_pct": (3, 0, 100_000, False),
        "term_months": (0, 1, 360, False),
    },
    "compound_int": {
        "principal": (2, 0, 10_000_000, False),
        "annual_rate_pct": (3, 0, 20_000, False),
        "compounds_per_year": (0, 1, 12, False),
        "years": (0, 1, 30, False),
    },
    "daily_ledger": {
        "opening_balance": (2, -10_000_000_000, 10_000_000_000, False),
        "deposits": (2, 0, 10_000_000_000, False),
        "withdrawals": (2, 0, 10_000_000_000, False),
        "overdraft_threshold": (2, -10_000_000_000, 10_000_000_000, True),
        "overdraft_fee": (2, 0, 9_999_999, False),
    },
}
OUTPUTS = {
    "loan_calc": ("monthly_payment", "total_interest", "total_payable"),
    "compound_int": ("maturity_amount", "accrued_interest"),
    "daily_ledger": ("closing_balance", "fee_applied", "overdraft_flag"),
}
PRESETS = tuple(SCHEMA)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def to_decimal(units: int, scale: int) -> Decimal:
    return Decimal(units).scaleb(-scale)


def fmt(units: int, scale: int) -> str:
    return str(units) if scale == 0 else f"{to_decimal(units, scale):.{scale}f}"


def post_raw(client, text: str):
    return client.post("/api/calculate", content=text, headers={"Content-Type": "application/json"})


def body_text(preset_id: str, values: dict[str, str], as_number: dict[str, bool]) -> str:
    """JSON text; fields flagged as_number are emitted as bare JSON numbers."""
    parts = []
    for name, text in values.items():
        parts.append(f'{json.dumps(name)}: {text if as_number.get(name) else json.dumps(text)}')
    return '{"preset_id": %s, "inputs": {%s}}' % (json.dumps(preset_id), ", ".join(parts))


@st.composite
def valid_requests(draw):
    preset = draw(st.sampled_from(PRESETS))
    units: dict[str, int] = {}
    for name, (scale, lo, hi, _d) in SCHEMA[preset].items():
        units[name] = draw(
            st.one_of(st.sampled_from([lo, hi, min(max(0, lo), hi)]), st.integers(lo, hi))
        )
    as_number = {n: draw(st.booleans()) for n in units}
    omit_threshold = preset == "daily_ledger" and draw(st.booleans())
    return preset, units, as_number, omit_threshold


def reference_for(preset: str, units: dict[str, int]) -> dict[str, Decimal]:
    kwargs = {
        n: (u if SCHEMA[preset][n][0] == 0 else to_decimal(u, SCHEMA[preset][n][0]))
        for n, u in units.items()
    }
    return reference_models.REGISTRY[preset](**kwargs)


# Feature: cobol-verification-engine, Property 19: valid inputs -> 200, equal to both models
@PBT_SETTINGS
@given(req=valid_requests())
def test_valid_inputs_match_reference_and_cobol(client, req):
    preset, units, as_number, omit_threshold = req
    if omit_threshold:
        units = {**units, "overdraft_threshold": 0}  # omitted == explicit 0.00 (18.8)
    sent = {
        n: fmt(u, SCHEMA[preset][n][0])
        for n, u in units.items()
        if not (omit_threshold and n == "overdraft_threshold")
    }
    resp = post_raw(client, body_text(preset, sent, as_number))
    assert resp.status_code == 200, resp.text
    data = json.loads(resp.text, parse_float=Decimal)
    assert data["preset_id"] == preset
    expected = reference_for(preset, units)
    assert tuple(data["result"]) == OUTPUTS[preset]
    tokens = data["raw_cobol_output"].split()
    assert len(tokens) == len(OUTPUTS[preset])
    for name, token in zip(OUTPUTS[preset], tokens):
        assert isinstance(data["result"][name], str)
        assert Decimal(data["result"][name]) == Decimal(expected[name])
        assert Decimal(token) == Decimal(expected[name])  # COBOL run agrees too


# Feature: cobol-verification-engine, Property 19: bad field -> 422 naming the field
@st.composite
def invalid_requests(draw):
    preset = draw(st.sampled_from(PRESETS))
    name = draw(st.sampled_from(sorted(SCHEMA[preset])))
    scale, lo, hi, has_default = SCHEMA[preset][name]
    kinds = ["non_numeric", "too_high", "too_low", "bad_type"]
    if not has_default:
        kinds.append("missing")
    if scale > 0:
        kinds.append("too_many_decimals")
    else:
        kinds.append("fractional_int")
    kind = draw(st.sampled_from(kinds))
    return preset, name, kind, draw(st.integers(1, 10_000))


@PBT_SETTINGS
@given(req=invalid_requests())
def test_invalid_field_gives_422_naming_field(client, req):
    preset, name, kind, extra = req
    scale, lo, hi, _ = SCHEMA[preset][name]
    values: dict[str, str] = {}
    numeric: dict[str, bool] = {}
    for n, (s, l, h, _d) in SCHEMA[preset].items():
        values[n] = fmt(max(l, min(h, 1)), s)
    if kind == "missing":
        del values[name]
    elif kind == "non_numeric":
        values[name] = draw_text(extra)
    elif kind == "too_high":
        values[name] = fmt(hi + extra, scale)
    elif kind == "too_low":
        values[name] = fmt(lo - extra, scale)
    elif kind == "bad_type":
        values[name] = ["true", "null", "[1]", '{"a": 1}'][extra % 4]
        numeric[name] = True
    elif kind == "too_many_decimals":
        values[name] = fmt(max(lo, min(hi, 1)), scale) + str(extra % 9 + 1)
    elif kind == "fractional_int":
        values[name] = f"{max(lo, min(hi, 1))}.{extra % 9 + 1}"
    resp = post_raw(client, body_text(preset, values, numeric))
    assert resp.status_code == 422, (kind, resp.text)
    assert resp.json()["detail"]["field"] == name


def draw_text(n: int) -> str:
    return ["abc", "", "NaN", "Infinity", "-Infinity", "1e", "12,5", "0x10", "--1", " "][n % 10]


# Feature: cobol-verification-engine, Property 19: unknown preset -> 404
@PBT_SETTINGS
@given(preset_id=st.text(max_size=40).filter(lambda s: s not in PRESETS))
def test_unknown_preset_gives_404(client, preset_id):
    body = json.dumps({"preset_id": preset_id, "inputs": {}})
    resp = post_raw(client, body)
    assert resp.status_code == 404


# Feature: cobol-verification-engine, Property 19: any perturbation -> 500 naming the field
@st.composite
def mismatch_requests(draw):
    preset, units, as_number, _ = draw(valid_requests())
    name = draw(st.sampled_from(OUTPUTS[preset]))
    delta = draw(st.sampled_from([Decimal("0.01"), Decimal("-0.01"), Decimal("1"), Decimal("1000")]))
    return preset, units, as_number, name, delta


@PBT_SETTINGS
@given(req=mismatch_requests())
def test_any_reference_mismatch_gives_500_naming_field(client, req):
    preset, units, as_number, name, delta = req
    real = reference_models.REGISTRY[preset]

    def perturbed(**kwargs):
        out = dict(real(**kwargs))
        out[name] = Decimal(out[name]) + delta
        return out

    sent = {n: fmt(u, SCHEMA[preset][n][0]) for n, u in units.items()}
    with mock.patch.dict(reference_models.REGISTRY, {preset: perturbed}):
        resp = post_raw(client, body_text(preset, sent, as_number))
    assert resp.status_code == 500, resp.text
    detail = resp.json()["detail"]
    assert detail["error"] == "parity_mismatch"
    assert detail["field"] == name
