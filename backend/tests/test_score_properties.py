"""Property 15: parity score and verified flag are exact functions of case counts.

# Feature: cobol-verification-engine, Property 15: Parity score and verified flag are correct arithmetic/logical functions of the case counts

**Validates: Requirements 13.2, 13.3, 13.4**
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from decimal import Decimal

import pytest
from hypothesis import given, settings, strategies as st

from app.pbt_verifier import ParityReport, compute_parity_score


def oracle_score(passed: int, total: int) -> str:
    """Pure-integer oracle: passed/total*100 to 2 places, round-half-even."""
    num = passed * 10000
    q, r = divmod(num, total)
    twice = 2 * r
    if twice > total or (twice == total and q % 2 == 1):
        q += 1
    return f"{q // 100}.{q % 100:02d}"


def _check(passed: int, total: int) -> None:
    failed = total - passed
    report = ParityReport.build(passed, failed, 1.0, [], [])
    assert report.total_cases == total
    assert report.passed_cases == passed
    assert report.failed_cases == failed == total - passed
    assert report.parity_score_pct == compute_parity_score(passed, total)
    assert report.parity_score_pct == oracle_score(passed, total)
    assert isinstance(report.parity_score_pct, str)
    assert Decimal(report.parity_score_pct) == Decimal(oracle_score(passed, total))
    verified = failed == 0
    assert report.verified is verified
    assert (report.parity_score_pct == "100.00") == verified


@st.composite
def counts(draw):
    total = draw(st.integers(min_value=1, max_value=10_000))
    passed = draw(st.integers(min_value=0, max_value=total))
    return passed, total


# Feature: cobol-verification-engine, Property 15: Parity score and verified flag are correct arithmetic/logical functions of the case counts
@settings(max_examples=200, database=None, deadline=None)
@given(counts())
def test_score_and_verified_match_integer_oracle(pt):
    _check(*pt)


# Feature: cobol-verification-engine, Property 15: edge cases at the verified boundary
@settings(max_examples=100, database=None, deadline=None)
@given(st.integers(min_value=1, max_value=500))
def test_one_failure_is_never_verified(total):
    report = ParityReport.build(total - 1, 1, 0.0, [], [])
    assert report.verified is False
    assert report.parity_score_pct != "100.00"


@pytest.mark.parametrize(
    "passed,total,expected",
    [(0, 1, "0.00"), (1, 3, "33.33"), (2, 3, "66.67"),
     (149, 150, "99.33"), (150, 150, "100.00"), (1, 8, "12.50"),
     (1, 1, "100.00"), (0, 150, "0.00")],
)
def test_known_scores(passed, total, expected):
    assert compute_parity_score(passed, total) == expected
    _check(passed, total)


def test_zero_total_not_verified():
    report = ParityReport.build(0, 0, 0.0, [], [])
    assert report.total_cases == 0
    assert report.verified is False


def test_no_float_in_score_functions():
    tree = ast.parse(textwrap.dedent(inspect.getsource(compute_parity_score)))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            assert not isinstance(node.value, float)
        if isinstance(node, ast.Name):
            assert node.id != "float"
