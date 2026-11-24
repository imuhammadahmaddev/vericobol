"""Property tests for the compound_int preset (COBOL vs Reference_Model).

Shared helpers sit at the top so later properties can reuse them. The
``compiled_presets`` fixture (conftest.py) compiles compound_int once per
module, and every property sends its cases to that single binary in one batch.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Context, Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.cobol_runner import CobolRunner
from app.reference_models import compound_int

# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------

CENT = Decimal("0.01")
OUTPUT_FIELDS = ("maturity_amount", "accrued_interest")
# Explicit context for the test's own arithmetic (never the ambient one).
CHECK_CONTEXT = Context(prec=60, rounding=ROUND_HALF_UP)

PBT_SETTINGS = settings(
    max_examples=100,
    database=None,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

# compound_int input schema: principal 9(9)V99, rate 9(3)V999, per_year 9(2),
# years 9(2). Ranges below stay far from overflow (worst case 20% annual for
# 30 years on 100,000.00 is about 4e7, under the 1e8 display limit of accrued).
principals = st.integers(min_value=0, max_value=10_000_000).map(
    lambda cents: Decimal(cents).scaleb(-2)
)
annual_rates = st.integers(min_value=0, max_value=20_000).map(
    lambda milli: Decimal(milli).scaleb(-3)
)
compounds_per_year = st.sampled_from([1, 2, 4, 12])
years = st.integers(min_value=1, max_value=30)
compound_cases = st.lists(
    st.tuples(principals, annual_rates, compounds_per_year, years),
    min_size=1,
    max_size=40,
)


def compound_binary(compiled_presets):
    """Module-scoped compound_int binary (compiled once); skip if absent."""
    if "compound_int" not in compiled_presets:
        pytest.skip("preset compound_int not available yet")
    return compiled_presets["compound_int"]


def format_row(principal: Decimal, annual_rate_pct: Decimal, per_year: int, yrs: int) -> str:
    return f"{principal:f} {annual_rate_pct:f} {per_year} {yrs}"


def run_compound_batch(binary, cases) -> list[dict[str, Decimal]]:
    """Run (principal, rate, per_year, years) cases through the COBOL binary in one process."""
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


# --------------------------------------------------------------------------
# Property 4
# --------------------------------------------------------------------------

# Feature: cobol-verification-engine, Property 4: Compound interest accrued interest is internally consistent
@PBT_SETTINGS
@given(cases=compound_cases)
def test_accrued_interest_equals_maturity_minus_principal(compiled_presets, cases):
    """**Validates: Requirements 5.5**

    For both the COBOL output and the Reference_Model output independently,
    accrued_interest == maturity_amount - principal.
    """
    binary = compound_binary(compiled_presets)
    cobol_rows = run_compound_batch(binary, cases)

    for case, cobol in zip(cases, cobol_rows):
        principal = case[0]
        python = compound_int(*case)
        for label, out in (("cobol", cobol), ("python", python)):
            expected = CHECK_CONTEXT.subtract(out["maturity_amount"], principal)
            assert out["accrued_interest"] == expected, (label, case, out, expected)


# --------------------------------------------------------------------------
# Property 5
# --------------------------------------------------------------------------

zero_rate_cases = st.lists(
    st.tuples(principals, st.just(Decimal("0")), compounds_per_year, years),
    min_size=1,
    max_size=40,
)


# Feature: cobol-verification-engine, Property 5: Zero-rate compounding leaves principal unchanged
@PBT_SETTINGS
@given(cases=zero_rate_cases)
def test_zero_rate_leaves_principal_unchanged(compiled_presets, cases):
    """**Validates: Requirements 5.6**

    With annual_rate_pct == 0, both COBOL and Reference_Model give
    maturity_amount == principal and accrued_interest == 0.00.
    """
    binary = compound_binary(compiled_presets)
    cobol_rows = run_compound_batch(binary, cases)

    for case, cobol in zip(cases, cobol_rows):
        principal = case[0]
        python = compound_int(*case)
        for label, out in (("cobol", cobol), ("python", python)):
            assert out["maturity_amount"] == principal, (label, case, out)
            assert out["accrued_interest"] == Decimal("0.00"), (label, case, out)
