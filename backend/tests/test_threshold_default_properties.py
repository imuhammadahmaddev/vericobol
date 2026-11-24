"""Property 8: omitted overdraft threshold behaves as zero (daily_ledger, POST /api/calculate)."""

from __future__ import annotations

import os

os.environ["VERICOBOL_SKIP_WARMUP"] = "1"  # must be set before importing app.main

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.cobol_runner import CobolRunner
from app.main import app
from app.reference_models import daily_ledger

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
OUTPUT_FIELDS = ("closing_balance", "fee_applied", "overdraft_flag")

PBT_SETTINGS = settings(
    max_examples=100,
    database=None,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)

# Schema ranges in cents (presets.json daily_ledger).
opening_c = st.integers(min_value=-10_000_000_000, max_value=10_000_000_000)
flow_c = st.integers(min_value=0, max_value=10_000_000_000)
fee_c = st.integers(min_value=0, max_value=9_999_999)
delta_c = st.one_of(st.sampled_from([-1, 0, 1]), st.integers(-1_000_000, 1_000_000))


def cents(n: int) -> Decimal:
    return Decimal(n).scaleb(-2).quantize(CENT)


@st.composite
def ledger_inputs(draw):
    """(opening, deposits, withdrawals, fee) in cents; net is often within 1 cent of 0."""
    opening = draw(opening_c)
    deposits = draw(flow_c)
    if draw(st.booleans()):
        withdrawals = draw(flow_c)
    else:  # aim net at 0 +/- delta, clamped into the withdrawals range
        withdrawals = min(max(opening + deposits - draw(delta_c), 0), 10_000_000_000)
    return opening, deposits, withdrawals, draw(fee_c)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _post(client, inputs: dict[str, str]) -> dict:
    resp = client.post("/api/calculate", json={"preset_id": "daily_ledger", "inputs": inputs})
    assert resp.status_code == 200, (inputs, resp.status_code, resp.text)
    return resp.json()


def _base(opening: Decimal, deposits: Decimal, withdrawals: Decimal, fee: Decimal) -> dict[str, str]:
    return {
        "opening_balance": f"{opening:f}",
        "deposits": f"{deposits:f}",
        "withdrawals": f"{withdrawals:f}",
        "overdraft_fee": f"{fee:f}",
    }


def _cobol_row(compiled_presets, opening, deposits, withdrawals, fee) -> dict[str, Decimal]:
    """Real COBOL run with threshold 0.00 in the input row."""
    if "daily_ledger" not in compiled_presets:
        pytest.skip("preset daily_ledger not available")
    row = f"{opening:f} {deposits:f} {withdrawals:f} {ZERO:f} {fee:f}"
    run = CobolRunner().run_batch(compiled_presets["daily_ledger"], [row])
    assert not run.timed_out and not run.truncated and run.exit_code == 0, run
    lines = run.stdout.splitlines()
    assert len(lines) == 1, run.stdout
    tokens = lines[0].split()
    assert len(tokens) == len(OUTPUT_FIELDS), lines[0]
    return {name: Decimal(tok) for name, tok in zip(OUTPUT_FIELDS, tokens)}


def check_omitted_equals_zero(client, compiled_presets, opening, deposits, withdrawals, fee) -> dict:
    base = _base(opening, deposits, withdrawals, fee)
    omitted = _post(client, dict(base))
    explicit = _post(client, {**base, "overdraft_threshold": "0.00"})

    # Identical responses: every result field and the raw COBOL output.
    assert omitted == explicit, (base, omitted, explicit)
    assert omitted["raw_cobol_output"] == explicit["raw_cobol_output"]

    # Equal to the Reference_Model with its default threshold.
    ref = daily_ledger(opening, deposits, withdrawals, overdraft_fee=fee)
    # Equal to the real COBOL row with threshold 0.00.
    cobol = _cobol_row(compiled_presets, opening, deposits, withdrawals, fee)
    for name in OUTPUT_FIELDS:
        got = Decimal(omitted["result"][name])
        assert got == Decimal(ref[name]), (name, base, got, ref[name])
        assert got == cobol[name], (name, base, got, cobol[name])
    raw = [Decimal(t) for t in omitted["raw_cobol_output"].split()]
    assert raw == [cobol[n] for n in OUTPUT_FIELDS], (base, omitted["raw_cobol_output"], cobol)
    return omitted


# Feature: cobol-verification-engine, Property 8: Omitted overdraft threshold behaves as zero
@PBT_SETTINGS
@given(data=ledger_inputs())
def test_omitted_threshold_equals_explicit_zero(client, compiled_presets, data):
    """**Validates: Requirements 6.8, 18.8**"""
    opening, deposits, withdrawals, fee = (cents(n) for n in data)
    check_omitted_equals_zero(client, compiled_presets, opening, deposits, withdrawals, fee)


# Feature: cobol-verification-engine, Property 8: Omitted overdraft threshold behaves as zero
@pytest.mark.parametrize(
    "withdrawals, flag, fee_applied",
    [("100.00", 0, "0.00"), ("100.01", 1, "25.00")],
    ids=["net_0.00", "net_-0.01"],
)
def test_default_threshold_boundary_examples(client, compiled_presets, withdrawals, flag, fee_applied):
    """**Validates: Requirements 6.8, 18.8**

    With the threshold omitted, net 0.00 is not overdrawn and net -0.01 is.
    """
    fee = Decimal("25.00")
    out = check_omitted_equals_zero(
        client, compiled_presets, Decimal("100.00"), ZERO, Decimal(withdrawals), fee
    )
    assert Decimal(out["result"]["overdraft_flag"]) == flag
    assert Decimal(out["result"]["fee_applied"]) == Decimal(fee_applied)
