"""Property tests for the daily_ledger preset (COBOL vs Reference_Model).

Shared helpers sit at the top so later properties can reuse them. The
``compiled_presets`` fixture (conftest.py) compiles daily_ledger once per
module, and every property sends its cases to that single binary in one batch.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.cobol_runner import CobolRunner
from app.reference_models import daily_ledger

# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
OUTPUT_FIELDS = ("closing_balance", "fee_applied", "overdraft_flag")

PBT_SETTINGS = settings(
    max_examples=100,
    database=None,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


def cents(n: int) -> Decimal:
    """Exact Decimal with scale 2 from an integer number of cents."""
    return Decimal(n).scaleb(-2).quantize(CENT)


# daily_ledger schema: opening S9(9)V99, deposits/withdrawals 9(9)V99,
# overdraft_threshold S9(9)V99, overdraft_fee 9(5)V99. Magnitudes are kept so
# the net balance and threshold never overflow their S9(9)V99 fields.
openings = st.integers(min_value=-5_000_000_000, max_value=5_000_000_000)
flows = st.integers(min_value=0, max_value=1_000_000_000)
fees = st.integers(min_value=0, max_value=9_999_999)
deltas = st.one_of(
    st.sampled_from([-1, 0, 1]),
    st.integers(min_value=-1_000_000, max_value=1_000_000),
)


@st.composite
def ledger_cases(draw):
    """(opening, deposits, withdrawals, threshold, fee) biased around the net balance."""
    opening = draw(openings)
    deposits = draw(flows)
    withdrawals = draw(flows)
    net = opening + deposits - withdrawals
    threshold = net + draw(deltas)  # delta in {-1,0,+1} hits the boundary exactly
    fee = draw(fees)
    return (cents(opening), cents(deposits), cents(withdrawals), cents(threshold), cents(fee))


def ledger_binary(compiled_presets):
    """Module-scoped daily_ledger binary (compiled once); skip if the preset is absent."""
    if "daily_ledger" not in compiled_presets:
        pytest.skip("preset daily_ledger not available yet")
    return compiled_presets["daily_ledger"]


def format_row(opening, deposits, withdrawals, threshold, fee) -> str:
    return f"{opening:f} {deposits:f} {withdrawals:f} {threshold:f} {fee:f}"


def run_ledger_batch(binary, cases) -> list[dict[str, Decimal]]:
    """Run ledger cases through the compiled COBOL in one process."""
    rows = [format_row(*case) for case in cases]
    result = CobolRunner().run_batch(binary, rows)
    assert not result.timed_out and not result.truncated, result
    assert result.exit_code == 0, result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == len(rows), (len(lines), len(rows), result.stderr)
    parsed = []
    for line in lines:
        values = [Decimal(tok) for tok in line.split()]
        assert len(values) == len(OUTPUT_FIELDS), line
        parsed.append(dict(zip(OUTPUT_FIELDS, values)))
    return parsed


def check_threshold_rule(binary, cases) -> None:
    """Assert fee/flag follow net < threshold for both COBOL and the reference model."""
    cobol_rows = run_ledger_batch(binary, cases)
    for case, cobol in zip(cases, cobol_rows):
        opening, deposits, withdrawals, threshold, fee = case
        below = opening + deposits - withdrawals < threshold
        expected_fee = fee if below else ZERO
        expected_flag = Decimal(1) if below else Decimal(0)
        python = daily_ledger(opening, deposits, withdrawals, threshold, fee)
        for source, out in (("cobol", cobol), ("python", python)):
            assert out["fee_applied"] == expected_fee, (source, case, out)
            assert out["overdraft_flag"] == expected_flag, (source, case, out)


# --------------------------------------------------------------------------
# Property 6
# --------------------------------------------------------------------------

# Feature: cobol-verification-engine, Property 6: Overdraft fee and flag follow the threshold comparison
@PBT_SETTINGS
@given(cases=st.lists(ledger_cases(), min_size=1, max_size=40))
def test_overdraft_fee_and_flag_follow_threshold(compiled_presets, cases):
    """**Validates: Requirements 6.4, 6.5, 19.11**"""
    check_threshold_rule(ledger_binary(compiled_presets), cases)


# Feature: cobol-verification-engine, Property 6: Overdraft fee and flag follow the threshold comparison
@pytest.mark.parametrize("threshold_cents", [0, 50_000, -50_000], ids=["zero", "positive", "negative"])
def test_overdraft_boundary_examples(compiled_presets, threshold_cents):
    """**Validates: Requirements 6.4, 6.5, 19.11**

    Net balance equal to, one cent below, and one cent above the threshold.
    """
    fee = Decimal("35.00")
    cases = [
        # opening = threshold + delta, no deposits/withdrawals
        (cents(threshold_cents + delta), ZERO, ZERO, cents(threshold_cents), fee)
        for delta in (0, -1, 1)
    ]
    check_threshold_rule(ledger_binary(compiled_presets), cases)
    rows = run_ledger_batch(ledger_binary(compiled_presets), cases)
    assert [r["overdraft_flag"] for r in rows] == [0, 1, 0]
    assert [r["fee_applied"] for r in rows] == [ZERO, fee, ZERO]


# Feature: cobol-verification-engine, Property 6: Overdraft fee and flag follow the threshold comparison
def test_net_zero_versus_minus_one_cent_at_default_threshold(compiled_presets):
    """**Validates: Requirements 6.4, 6.5, 19.11**

    With the default threshold 0.00, net 0.00 is not overdrawn; net -0.01 is.
    """
    fee = Decimal("25.00")
    cases = [
        (Decimal("100.00"), ZERO, Decimal("100.00"), ZERO, fee),   # net 0.00
        (Decimal("100.00"), ZERO, Decimal("100.01"), ZERO, fee),   # net -0.01
    ]
    rows = run_ledger_batch(ledger_binary(compiled_presets), cases)
    assert [r["overdraft_flag"] for r in rows] == [0, 1]
    assert [r["fee_applied"] for r in rows] == [ZERO, fee]
    check_threshold_rule(ledger_binary(compiled_presets), cases)


# --------------------------------------------------------------------------
# Property 7
# --------------------------------------------------------------------------

def check_closing_consistency(binary, cases) -> None:
    """closing == opening + deposits - withdrawals - fee_applied, for COBOL and Python."""
    cobol_rows = run_ledger_batch(binary, cases)
    for case, cobol in zip(cases, cobol_rows):
        opening, deposits, withdrawals, threshold, fee = case
        python = daily_ledger(opening, deposits, withdrawals, threshold, fee)
        for source, out in (("cobol", cobol), ("python", python)):
            expected = opening + deposits - withdrawals - out["fee_applied"]
            assert out["closing_balance"] == expected, (source, case, out)


# Feature: cobol-verification-engine, Property 7: Closing balance is internally consistent
@PBT_SETTINGS
@given(cases=st.lists(ledger_cases(), min_size=1, max_size=40))
def test_closing_balance_is_internally_consistent(compiled_presets, cases):
    """**Validates: Requirements 6.6**"""
    check_closing_consistency(ledger_binary(compiled_presets), cases)
