"""Property test for deterministic Hypothesis case generation (task 7.4)."""
from __future__ import annotations

from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.pbt_verifier import MAX_CASES, MIN_CASES, generate_cases

PRESETS = ["loan_calc", "compound_int", "daily_ledger"]

_SETTINGS = settings(
    max_examples=100,
    database=None,
    deadline=None,
    suppress_health_check=list(HealthCheck),
)

ZERO = Decimal("0")
CENT = Decimal("0.01")


def _rows(cases):
    return [(c.preset_id, tuple(c.inputs.items())) for c in cases]


def _has(cases, **expected) -> bool:
    return any(all(c.inputs[k] == v for k, v in expected.items()) for c in cases)


def _net(c):
    i = c.inputs
    return i["opening_balance"] + i["deposits"] - i["withdrawals"]


def _assert_required_edges(preset_id, cases):
    if preset_id == "loan_calc":
        assert any(c.inputs["principal"] == CENT for c in cases)
        assert any(c.inputs["annual_rate_pct"] == ZERO for c in cases)
        assert any(c.inputs["term_months"] == 1 for c in cases)
        assert any(c.inputs["annual_rate_pct"] == Decimal("3.333") for c in cases)
    elif preset_id == "compound_int":
        assert any(c.inputs["principal"] == ZERO for c in cases)
        assert any(c.inputs["principal"] == CENT for c in cases)
        assert any(c.inputs["annual_rate_pct"] == ZERO for c in cases)
        assert any(c.inputs["annual_rate_pct"] == Decimal("3.333") for c in cases)
        assert any(c.inputs["years"] == 1 for c in cases)
    else:
        zero_th = [c for c in cases if c.inputs["overdraft_threshold"] == ZERO]
        assert any(_net(c) == ZERO for c in zero_th)
        assert any(_net(c) == -CENT for c in zero_th)
        for delta in (ZERO, -CENT, CENT):
            assert any(
                c.inputs["overdraft_threshold"] != ZERO
                and _net(c) == c.inputs["overdraft_threshold"] + delta
                for c in cases
            ), f"missing threshold-relative case delta={delta}"
        assert any(c.inputs["overdraft_threshold"] < ZERO for c in cases)
        assert any(c.inputs["overdraft_threshold"] > ZERO for c in cases)


# Feature: cobol-verification-engine, Property 17: Hypothesis case generation is deterministic under a fixed seed
# **Validates: Requirements 11.8**
@_SETTINGS
@given(
    seed=st.integers(min_value=0, max_value=2**32 - 1),
    preset_id=st.sampled_from(PRESETS),
    num_cases=st.integers(min_value=MIN_CASES, max_value=MAX_CASES),
)
def test_generation_deterministic_under_fixed_seed(seed, preset_id, num_cases):
    first = generate_cases(preset_id, num_cases, seed)
    second = generate_cases(preset_id, num_cases, seed)

    assert first == second
    assert _rows(first) == _rows(second)
    assert len(first) == num_cases
    assert len({_rows([c])[0] for c in first}) == num_cases

    for c in first:
        assert c.preset_id == preset_id
        for name, v in c.inputs.items():
            assert isinstance(v, (Decimal, int)) and not isinstance(v, (float, bool)), (name, v)

    _assert_required_edges(preset_id, first)


# Feature: cobol-verification-engine, Property 17: Hypothesis case generation is deterministic under a fixed seed
# **Validates: Requirements 11.8**
@_SETTINGS
@given(
    seeds=st.tuples(
        st.integers(min_value=0, max_value=2**32 - 1),
        st.integers(min_value=0, max_value=2**32 - 1),
    ).filter(lambda t: t[0] != t[1]),
    preset_id=st.sampled_from(PRESETS),
)
def test_different_seeds_differ(seeds, preset_id):
    a = generate_cases(preset_id, MIN_CASES, seeds[0])
    b = generate_cases(preset_id, MIN_CASES, seeds[1])
    assert _rows(a) != _rows(b)
