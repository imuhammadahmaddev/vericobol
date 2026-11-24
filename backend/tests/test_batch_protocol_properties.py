"""Property 1: the batch protocol preserves row count and order.

**Validates: Requirements 3.3**

Each Preset binary is compiled once per module (``compiled_presets`` fixture).
Every Hypothesis example is one batched ``run_batch`` call of 1 to 250 rows.
Line i of the output must be the result for input row i, which is checked
against the Reference_Model for row i.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from app import reference_models
from app.cobol_runner import CobolRunner

from .conftest import SAMPLES_DIR

MAX_ROWS = 250

with (SAMPLES_DIR / "presets.json").open(encoding="utf-8") as _fh:
    _CATALOG = json.load(_fh)


def _bounds(preset_id: str) -> dict[str, tuple[Decimal, Decimal]]:
    return {
        f["name"]: (Decimal(f["min"]), Decimal(f["max"]))
        for f in _CATALOG[preset_id]["input_schema"]
    }


def _decimals(lo: Decimal, hi: Decimal, places: int) -> st.SearchStrategy[Decimal]:
    """Decimals in [lo, hi] with ``places`` fraction digits, built from integers."""
    unit = Decimal(1).scaleb(-places)
    lo_i = int(lo / unit)
    hi_i = int(hi / unit)
    return st.integers(min_value=lo_i, max_value=hi_i).map(lambda i: Decimal(i).scaleb(-places))


def _ints(lo: Decimal, hi: Decimal) -> st.SearchStrategy[int]:
    return st.integers(min_value=int(lo), max_value=int(hi))


def _fmt(value: Decimal, places: int) -> str:
    return f"{value:.{places}f}"


_LB = _bounds("loan_calc")
_CB = _bounds("compound_int")
_DB = _bounds("daily_ledger")

loan_rows = st.tuples(
    _decimals(*_LB["principal"], 2),
    _decimals(*_LB["annual_rate_pct"], 3),
    _ints(*_LB["term_months"]),
)
compound_rows = st.tuples(
    _decimals(*_CB["principal"], 2),
    _decimals(*_CB["annual_rate_pct"], 3),
    _ints(*_CB["compounds_per_year"]),
    _ints(*_CB["years"]),
)
ledger_rows = st.tuples(
    _decimals(*_DB["opening_balance"], 2),
    _decimals(*_DB["deposits"], 2),
    _decimals(*_DB["withdrawals"], 2),
    _decimals(*_DB["overdraft_threshold"], 2),
    _decimals(*_DB["overdraft_fee"], 2),
)


def _loan_line(row) -> str:
    return f"{_fmt(row[0], 2)} {_fmt(row[1], 3)} {row[2]}"


def _compound_line(row) -> str:
    return f"{_fmt(row[0], 2)} {_fmt(row[1], 3)} {row[2]} {row[3]}"


def _ledger_line(row) -> str:
    return " ".join(_fmt(v, 2) for v in row)


def _loan_expected(row) -> list[Decimal]:
    r = reference_models.loan_calc(*row)
    return [r["monthly_payment"], r["total_interest"], r["total_payable"]]


def _compound_expected(row) -> list[Decimal]:
    r = reference_models.compound_int(*row)
    return [r["maturity_amount"], r["accrued_interest"]]


def _ledger_expected(row) -> list[Decimal]:
    r = reference_models.daily_ledger(*row)
    return [r["closing_balance"], r["fee_applied"], r["overdraft_flag"]]


@pytest.fixture(scope="module")
def runner(cobc_path: str) -> CobolRunner:
    return CobolRunner(cobc_path)


def _check_batch(runner, binary, rows, to_line, expected_fn) -> None:
    result = runner.run_batch(binary, [to_line(r) for r in rows])
    assert not result.timed_out and not result.truncated
    assert result.exit_code == 0, result.stderr
    lines = result.stdout.splitlines()
    # Output row count equals input row count.
    assert len(lines) == len(rows), f"{len(lines)} output lines for {len(rows)} rows"
    # Output row i corresponds to input row i.
    for i, (row, line) in enumerate(zip(rows, lines)):
        got = [Decimal(tok) for tok in line.split()]
        assert got == expected_fn(row), f"row {i}: input {to_line(row)!r} -> {line!r}"


_SETTINGS = dict(
    max_examples=100,
    database=None,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large,
                           HealthCheck.function_scoped_fixture],
)


# Feature: cobol-verification-engine, Property 1: Batch protocol preserves row count and order
@settings(**_SETTINGS)
@example(rows=[(Decimal("0.01"), Decimal("0.000"), 1)])
@example(rows=[(Decimal("10000000.00"), Decimal("100.000"), 360)] * MAX_ROWS)
@given(rows=st.lists(loan_rows, min_size=1, max_size=MAX_ROWS))
def test_loan_calc_batch_preserves_count_and_order(require_preset, runner, rows):
    _check_batch(runner, require_preset("loan_calc"), rows, _loan_line, _loan_expected)


# Feature: cobol-verification-engine, Property 1: Batch protocol preserves row count and order
@settings(**_SETTINGS)
@example(rows=[(Decimal("0.00"), Decimal("0.000"), 1, 1)])
@example(rows=[(Decimal("100000.00"), Decimal("20.000"), 12, 30)] * MAX_ROWS)
@given(rows=st.lists(compound_rows, min_size=1, max_size=MAX_ROWS))
def test_compound_int_batch_preserves_count_and_order(require_preset, runner, rows):
    _check_batch(runner, require_preset("compound_int"), rows, _compound_line, _compound_expected)


# Feature: cobol-verification-engine, Property 1: Batch protocol preserves row count and order
@settings(**_SETTINGS)
@example(rows=[(Decimal("0.00"), Decimal("0.00"), Decimal("0.00"), Decimal("0.00"), Decimal("0.00"))])
@example(
    rows=[(Decimal("-100000000.00"), Decimal("0.00"), Decimal("100000000.00"),
           Decimal("100000000.00"), Decimal("99999.99"))] * MAX_ROWS
)
@given(rows=st.lists(ledger_rows, min_size=1, max_size=MAX_ROWS))
def test_daily_ledger_batch_preserves_count_and_order(require_preset, runner, rows):
    _check_batch(runner, require_preset("daily_ledger"), rows, _ledger_line, _ledger_expected)
