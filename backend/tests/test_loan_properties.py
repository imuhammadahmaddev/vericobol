"""Property tests for the loan_calc preset (COBOL vs Reference_Model).

Shared helpers sit at the top so later properties can reuse them. The
``compiled_presets`` fixture (conftest.py) compiles loan_calc once per module,
and every property sends its cases to that single binary in one batch.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Context, Decimal

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from app.cobol_runner import CobolRunner
from app.reference_models import loan_calc

# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------

CENT = Decimal("0.01")
OUTPUT_FIELDS = ("monthly_payment", "total_interest", "total_payable")
# Explicit context for the test's own arithmetic (never the ambient one).
CHECK_CONTEXT = Context(prec=60, rounding=ROUND_HALF_UP)

PBT_SETTINGS = settings(
    max_examples=100,
    database=None,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

# loan_calc input schema: principal 9(9)V99, term_months 9(3).
principals = st.integers(min_value=0, max_value=99_999_999_999).map(
    lambda cents: Decimal(cents).scaleb(-2)
)
term_months = st.integers(min_value=1, max_value=999)
zero_rate_cases = st.lists(st.tuples(principals, term_months), min_size=1, max_size=40)


def loan_binary(compiled_presets):
    """Module-scoped loan_calc binary (compiled once); skip if the preset is absent."""
    if "loan_calc" not in compiled_presets:
        pytest.skip("preset loan_calc not available yet")
    return compiled_presets["loan_calc"]


def format_row(principal: Decimal, annual_rate_pct: Decimal, term: int) -> str:
    return f"{principal:f} {annual_rate_pct:f} {term}"


def run_loan_batch(binary, cases) -> list[dict[str, Decimal]]:
    """Run (principal, rate, term) cases through the compiled COBOL in one process."""
    rows = [format_row(p, r, t) for p, r, t in cases]
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


# --------------------------------------------------------------------------
# Property 2
# --------------------------------------------------------------------------

# Feature: cobol-verification-engine, Property 2: Zero-rate loan payment equals straight division
@PBT_SETTINGS
@given(cases=zero_rate_cases)
def test_zero_rate_payment_equals_straight_division(compiled_presets, cases):
    """**Validates: Requirements 4.3**

    With annual_rate_pct = 0, monthly_payment == principal / term_months,
    stored ROUNDED (half-up) at scale 2, as loan_calc.cbl does.
    """
    binary = loan_binary(compiled_presets)
    zero = Decimal("0")
    cobol_rows = run_loan_batch(binary, [(p, zero, t) for p, t in cases])

    for (principal, term), cobol in zip(cases, cobol_rows):
        expected = CHECK_CONTEXT.divide(principal, Decimal(term)).quantize(
            CENT, rounding=ROUND_HALF_UP, context=CHECK_CONTEXT
        )
        python = loan_calc(principal, zero, term)["monthly_payment"]
        assert cobol["monthly_payment"] == expected, (principal, term, cobol, expected)
        assert python == expected, (principal, term, python, expected)


# --------------------------------------------------------------------------
# Property 3
# --------------------------------------------------------------------------

MAX_FIELD = Decimal("999999999.99")  # PIC 9(9)V99 capacity
# Rates in thousandths of a percent (PIC 9(3)V999), biased to realistic values
# and including the repeating-decimal case 3.333.
rates = st.one_of(
    st.just(Decimal("3.333")),
    st.integers(min_value=1, max_value=30_000).map(lambda m: Decimal(m).scaleb(-3)),
)
consistency_cases = st.lists(
    st.tuples(principals, rates, term_months), min_size=1, max_size=40
)


# Feature: cobol-verification-engine, Property 3: Loan derived totals are internally consistent
@PBT_SETTINGS
@given(cases=consistency_cases)
def test_loan_derived_totals_are_consistent(compiled_presets, cases):
    """**Validates: Requirements 4.6**

    total_payable == monthly_payment * term_months and
    total_interest == total_payable - principal, exactly, for the COBOL output
    and the Reference_Model output independently.

    Domain: non-overflowing cases only. The outputs are unsigned PIC 9(9)V99,
    so a case whose exact total_payable - principal is negative (payment
    rounded down) or whose total exceeds the field is not representable and is
    excluded.
    """
    binary = loan_binary(compiled_presets)
    cobol_rows = run_loan_batch(binary, cases)

    checked = 0
    for (principal, rate, term), cobol in zip(cases, cobol_rows):
        python = loan_calc(principal, rate, term)
        exact_total = python["monthly_payment"] * term
        if exact_total > MAX_FIELD or exact_total < principal:
            continue
        checked += 1
        for label, out in (("cobol", cobol), ("python", python)):
            total = CHECK_CONTEXT.multiply(out["monthly_payment"], Decimal(term))
            interest = CHECK_CONTEXT.subtract(out["total_payable"], principal)
            assert out["total_payable"] == total, (label, principal, rate, term, out)
            assert out["total_interest"] == interest, (label, principal, rate, term, out)
        assert cobol == python, (principal, rate, term, cobol, python)
    assume(checked > 0)
