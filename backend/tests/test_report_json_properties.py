"""Property test for the Parity_Report JSON round trip (task 7.10).

# Feature: cobol-verification-engine, Property 16: Parity report survives a Decimal-aware JSON round trip

**Validates: Requirements 13.10, 19.9**
"""

from __future__ import annotations

import json
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from app.pbt_verifier import (
    LIKELY_CAUSES,
    Counterexample,
    ParityReport,
    compute_parity_score,
    run_verification,
)


@st.composite
def money_text(draw) -> str:
    """Decimal text: zero, negative, 38-digit, trailing zeros, many decimals."""
    kind = draw(st.sampled_from(["zero", "plain", "big", "trailing", "many"]))
    if kind == "zero":
        return draw(st.sampled_from(["0", "0.0", "0.00", "-0.00", "0.000"]))
    sign = draw(st.sampled_from(["", "-"]))
    if kind == "big":
        digits = draw(st.integers(min_value=10**37, max_value=10**38 - 1))
        scale = draw(st.integers(min_value=0, max_value=10))
        s = str(digits)
        return sign + (s[:-scale] + "." + s[-scale:] if scale else s)
    if kind == "trailing":
        whole = draw(st.integers(min_value=0, max_value=10**9))
        frac = draw(st.integers(min_value=0, max_value=99))
        return f"{sign}{whole}.{frac:02d}" + "0" * draw(st.integers(1, 20))
    if kind == "many":
        whole = draw(st.integers(min_value=0, max_value=10**6))
        frac = draw(st.text(alphabet="0123456789", min_size=10, max_size=40))
        return f"{sign}{whole}.{frac}"
    whole = draw(st.integers(min_value=0, max_value=10**12))
    frac = draw(st.integers(min_value=0, max_value=99))
    return f"{sign}{whole}.{frac:02d}"


_names = st.sampled_from(["principal", "rate", "balance", "payment", "fee", "total"])


def _money_dict(min_size: int = 1):
    return st.dictionaries(_names, money_text(), min_size=min_size, max_size=4)


counterexamples = st.builds(
    Counterexample,
    inputs=_money_dict(),
    cobol_output=_money_dict(),
    python_output=_money_dict(),
    differing_field=_names,
    penny_delta=st.one_of(money_text(), st.just("n/a")),
    likely_cause=st.sampled_from(sorted(LIKELY_CAUSES)),
)

raw_lines = st.lists(
    st.text(
        alphabet=st.characters(blacklist_categories=("Cs",)), max_size=60
    ),
    max_size=20,
)


@st.composite
def reports(draw) -> ParityReport:
    passed = draw(st.integers(min_value=0, max_value=250))
    failed = draw(st.integers(min_value=0, max_value=250 - passed))
    total = passed + failed
    return ParityReport(
        total_cases=total,
        passed_cases=passed,
        failed_cases=failed,
        parity_score_pct=compute_parity_score(passed, total),
        verified=failed == 0 and total > 0,
        execution_ms=round(draw(st.floats(0, 1e5, allow_nan=False)), 1),
        raw_cobol_sample_output=draw(raw_lines),
        counterexamples=draw(st.lists(counterexamples, max_size=5)),
    )


def _no_float_money(text: str) -> None:
    """Parse with parse_float=Decimal; every float-like value must be a Decimal."""
    data = json.loads(text, parse_float=Decimal)
    # Money values are JSON strings: parsing as float must never be invoked
    # for them, so a parser that rejects floats may only see execution_ms.
    seen: list[str] = []
    json.loads(text, parse_float=lambda s: seen.append(s) or Decimal(s))
    assert len(seen) <= 1, f"unexpected JSON numbers with fraction: {seen}"
    return data


# Feature: cobol-verification-engine, Property 16: Parity report survives a Decimal-aware JSON round trip
@settings(max_examples=100, database=None, deadline=None)
@given(report=reports())
def test_report_json_round_trip(report: ParityReport) -> None:
    text = report.to_json()
    data = _no_float_money(text)
    restored = ParityReport.from_json(text)
    assert restored == report
    # Scale and trailing zeros are preserved exactly (strings stay strings).
    for orig, back in zip(report.counterexamples, restored.counterexamples):
        for a, b in ((orig.inputs, back.inputs),
                     (orig.cobol_output, back.cobol_output),
                     (orig.python_output, back.python_output)):
            assert list(a.items()) == list(b.items())
    for cx in data["counterexamples"]:
        for key in ("inputs", "cobol_output", "python_output"):
            assert all(isinstance(v, str) for v in cx[key].values())
        assert orig.penny_delta == back.penny_delta
    assert restored.parity_score_pct == report.parity_score_pct


def test_trailing_zero_string_preserved() -> None:
    cx = Counterexample(
        inputs={"principal": "10.50"}, cobol_output={"payment": "0.00"},
        python_output={"payment": "-0.010"}, differing_field="payment",
        penny_delta="0.01", likely_cause="rounded_vs_truncation",
    )
    report = ParityReport.build(0, 1, 1.5, ["10.50"], [cx])
    back = ParityReport.from_json(report.to_json())
    assert back == report
    assert back.counterexamples[0].inputs["principal"] == "10.50"


def test_real_run_verification_round_trip() -> None:
    report = run_verification("loan_calc", None, num_cases=150, seed=7)
    text = report.to_json()
    _no_float_money(text)
    assert ParityReport.from_json(text) == report
    bad = run_verification("loan_calc", None, num_cases=150, seed=7,
                           simulate_float_bug=True)
    assert bad.counterexamples
    assert ParityReport.from_json(bad.to_json()) == bad
