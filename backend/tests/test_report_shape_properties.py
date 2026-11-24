"""Property test for the Parity_Report shape (task 7.8).

Runs the real GnuCOBOL preset binaries (no mocks) against the Reference_Model,
the Float_Bug_Demo and randomly perturbed target functions.

max_examples is 100 (the requested minimum); each example is one full
verification of 150-250 Cases.
"""

from __future__ import annotations

import json
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from app import reference_models
from app.pbt_verifier import (
    LIKELY_CAUSES,
    MAX_CASES,
    MIN_CASES,
    ParityReport,
    run_verification,
)

from .conftest import PRESET_IDS

_MONEY_TEXT_FIELDS = ("parity_score_pct",)


def _perturbed(preset_id: str, mode: str, every: int, field_idx: int, delta_cents: int):
    """Reference model that misbehaves on every ``every``-th call."""
    base = reference_models.REGISTRY[preset_id]
    calls = {"n": 0}

    def fn(**inputs):
        calls["n"] += 1
        out = dict(base(**inputs))
        if calls["n"] % every:
            return out
        names = list(out)
        name = names[field_idx % len(names)]
        if mode == "shift":
            out[name] = Decimal(out[name]) + Decimal(delta_cents).scaleb(-2)
        elif mode == "raise":
            raise ArithmeticError("boom")
        elif mode == "drop":
            del out[name]
        elif mode == "nan":
            out[name] = Decimal("NaN")
        return out

    return fn


def _assert_shape(report: ParityReport, num_cases: int) -> None:
    d = report.to_dict()
    required = {
        "total_cases": int, "passed_cases": int, "failed_cases": int,
        "parity_score_pct": str, "verified": bool, "execution_ms": (int, float),
        "raw_cobol_sample_output": list, "counterexamples": list,
    }
    assert set(d) == set(required)
    for key, typ in required.items():
        assert isinstance(d[key], typ), key
    assert not isinstance(d["total_cases"], bool)

    assert d["total_cases"] == num_cases
    assert d["total_cases"] == d["passed_cases"] + d["failed_cases"]
    assert d["passed_cases"] >= 0 and d["failed_cases"] >= 0
    assert d["verified"] == (d["failed_cases"] == 0)
    assert d["execution_ms"] >= 0
    score = Decimal(d["parity_score_pct"])
    assert Decimal("0.00") <= score <= Decimal("100.00")
    assert d["parity_score_pct"].count(".") == 1
    assert len(d["parity_score_pct"].split(".")[1]) == 2

    assert len(d["raw_cobol_sample_output"]) <= 20
    assert all(isinstance(x, str) for x in d["raw_cobol_sample_output"])
    assert len(d["counterexamples"]) <= 5
    assert (d["failed_cases"] > 0) == (len(d["counterexamples"]) > 0)
    for ce in d["counterexamples"]:
        assert set(ce) == {"inputs", "cobol_output", "python_output",
                           "differing_field", "penny_delta", "likely_cause"}
        assert ce["likely_cause"] in LIKELY_CAUSES
        assert isinstance(ce["differing_field"], str)
        assert isinstance(ce["penny_delta"], str)
        for sub in ("inputs", "cobol_output", "python_output"):
            assert isinstance(ce[sub], dict) and ce[sub]
            assert all(isinstance(k, str) and isinstance(v, str)
                       for k, v in ce[sub].items())

    # Serialized form: no JSON float anywhere except the execution_ms timing.
    parsed = json.loads(report.to_json(), parse_float=Decimal)
    assert isinstance(parsed["execution_ms"], (Decimal, int))
    parsed.pop("execution_ms")
    stack = [parsed]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
        else:
            assert not isinstance(item, Decimal), item  # would have been a JSON float
    # Round trip preserves the report.
    assert ParityReport.from_json(report.to_json()).to_dict() == d


# Feature: cobol-verification-engine, Property 14: Parity report shape is always well-formed
# **Validates: Requirements 13.1, 13.5, 13.6, 13.7, 13.9**
@settings(max_examples=100, database=None, deadline=None)
@given(
    preset_id=st.sampled_from(PRESET_IDS),
    num_cases=st.integers(MIN_CASES, MAX_CASES),
    seed=st.integers(0, 2**32 - 1),
    target=st.sampled_from(["reference", "float_bug", "perturbed"]),
    mode=st.sampled_from(["shift", "raise", "drop", "nan"]),
    every=st.integers(1, 40),
    field_idx=st.integers(0, 2),
    delta_cents=st.integers(-100_000, 100_000).filter(lambda x: x != 0),
)
def test_parity_report_shape_is_well_formed(
    preset_id, num_cases, seed, target, mode, every, field_idx, delta_cents
):
    kwargs = {"num_cases": num_cases, "seed": seed}
    if target == "float_bug":
        kwargs["simulate_float_bug"] = True
    elif target == "perturbed":
        kwargs["target_fn"] = _perturbed(preset_id, mode, every, field_idx, delta_cents)
    report = run_verification(preset_id, None, **kwargs)
    _assert_shape(report, num_cases)
