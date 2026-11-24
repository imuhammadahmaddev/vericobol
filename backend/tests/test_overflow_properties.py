"""Property 10: Reference_Model overflow behavior matches COBOL.

Inputs sit at or near the capacity of the input PIC clauses (principal and
balances up to 999999999.99, rates up to 999.999, long terms) so that results
reach or exceed the receiving fields' capacity. No preset has ON SIZE ERROR, so
COBOL silently drops high-order digits; the Reference_Model must do the same.
Every output field must equal the real compiled binary's output exactly.
Decimal only, no float.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.cobol_runner import CobolRunner
from app.reference_models import compound_int, daily_ledger, loan_calc

PBT_SETTINGS = settings(
    max_examples=100,
    database=None,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

LOAN_FIELDS = ("monthly_payment", "total_interest", "total_payable")
COMPOUND_FIELDS = ("maturity_amount", "accrued_interest")
LEDGER_FIELDS = ("closing_balance", "fee_applied", "overdraft_flag")


def cents(n: int) -> Decimal:
    return Decimal(n).scaleb(-2)


def thousandths(n: int) -> Decimal:
    return Decimal(n).scaleb(-3)


# --------------------------------------------------------------------------
# Strategies (integers of the smallest unit, so no float is ever involved)
# --------------------------------------------------------------------------

MAX_9_9_V2 = 99_999_999_999   # PIC 9(9)V99 in cents
MAX_RATE = 999_999            # PIC 9(3)V999 in thousandths

# Near-capacity amounts: exactly max, just under max, or anywhere in the range.
big_amounts = st.one_of(
    st.just(MAX_9_9_V2),
    st.integers(min_value=MAX_9_9_V2 - 1_000_000, max_value=MAX_9_9_V2),
    st.integers(min_value=0, max_value=MAX_9_9_V2),
).map(cents)

big_rates = st.one_of(
    st.just(thousandths(MAX_RATE)),
    st.integers(min_value=0, max_value=MAX_RATE).map(thousandths),
    st.integers(min_value=100_000, max_value=MAX_RATE).map(thousandths),
)

long_terms = st.one_of(st.just(999), st.integers(min_value=1, max_value=999))

loan_cases = st.lists(st.tuples(big_amounts, big_rates, long_terms), min_size=1, max_size=15)

compound_cases = st.lists(
    st.tuples(
        big_amounts,
        big_rates,
        st.integers(min_value=1, max_value=99),
        st.integers(min_value=1, max_value=99),
    ),
    min_size=1,
    max_size=4,
)

signed_big = st.one_of(
    st.just(MAX_9_9_V2),
    st.just(-MAX_9_9_V2),
    st.integers(min_value=-MAX_9_9_V2, max_value=MAX_9_9_V2),
).map(cents)
fees = st.one_of(st.just(9_999_999), st.integers(min_value=0, max_value=9_999_999)).map(cents)

ledger_cases = st.lists(
    st.tuples(signed_big, big_amounts, big_amounts, signed_big, fees),
    min_size=1,
    max_size=20,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def binary_for(compiled_presets, preset_id: str):
    if preset_id not in compiled_presets:
        pytest.skip(f"preset {preset_id} not available yet")
    return compiled_presets[preset_id]


def run_batch(binary, rows: list[str], fields: tuple[str, ...]) -> list[dict[str, Decimal]]:
    result = CobolRunner().run_batch(binary, rows)
    assert not result.timed_out and not result.truncated, result
    assert result.exit_code == 0, result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == len(rows), (len(lines), len(rows), result.stderr)
    parsed = []
    for line in lines:
        values = [Decimal(tok) for tok in line.split()]
        assert len(values) == len(fields), line
        parsed.append(dict(zip(fields, values)))
    return parsed


# --------------------------------------------------------------------------
# Property 10
# --------------------------------------------------------------------------

# Feature: cobol-verification-engine, Property 10: Reference_Model overflow behavior matches COBOL
@PBT_SETTINGS
@given(cases=loan_cases)
def test_loan_overflow_matches_cobol(compiled_presets, cases):
    """**Validates: Requirements 10.6**"""
    binary = binary_for(compiled_presets, "loan_calc")
    rows = [f"{p:f} {r:f} {t}" for p, r, t in cases]
    cobol_rows = run_batch(binary, rows, LOAN_FIELDS)
    for (p, r, t), cobol in zip(cases, cobol_rows):
        python = loan_calc(p, r, t)
        assert python == cobol, {"inputs": (p, r, t), "cobol": cobol, "python": python}


# Feature: cobol-verification-engine, Property 10: Reference_Model overflow behavior matches COBOL
@PBT_SETTINGS
@given(cases=compound_cases)
def test_compound_overflow_matches_cobol(compiled_presets, cases):
    """**Validates: Requirements 10.6**"""
    binary = binary_for(compiled_presets, "compound_int")
    rows = [f"{p:f} {r:f} {k} {y}" for p, r, k, y in cases]
    cobol_rows = run_batch(binary, rows, COMPOUND_FIELDS)
    for (p, r, k, y), cobol in zip(cases, cobol_rows):
        python = compound_int(p, r, k, y)
        assert python == cobol, {"inputs": (p, r, k, y), "cobol": cobol, "python": python}


# Feature: cobol-verification-engine, Property 10: Reference_Model overflow behavior matches COBOL
@PBT_SETTINGS
@given(cases=ledger_cases)
def test_ledger_overflow_matches_cobol(compiled_presets, cases):
    """**Validates: Requirements 10.6**"""
    binary = binary_for(compiled_presets, "daily_ledger")
    rows = [f"{o:f} {d:f} {w:f} {t:f} {f:f}" for o, d, w, t, f in cases]
    cobol_rows = run_batch(binary, rows, LEDGER_FIELDS)
    for (o, d, w, t, f), cobol in zip(cases, cobol_rows):
        python = daily_ledger(o, d, w, t, f)
        assert python == cobol, {"inputs": (o, d, w, t, f), "cobol": cobol, "python": python}
