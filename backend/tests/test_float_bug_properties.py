"""Property 13: the float-bug demonstration always diverges.

Runs the real GnuCOBOL preset binary (via ``run_verification``) against each
preset's Float_Bug_Demo and against the Decimal Reference_Model with the same
seed. Floats appear only inside the float-bug functions under test.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.pbt_verifier import LIKELY_CAUSES, MAX_COUNTEREXAMPLES, run_verification

PRESETS = ("loan_calc", "compound_int", "daily_ledger")
ALLOWED_CAUSES = LIKELY_CAUSES - {"python_error"}

# 100 examples per preset; each example runs two full verifications of 150-250 cases.
PBT_SETTINGS = settings(
    max_examples=100,
    database=None,
    deadline=None,
    suppress_health_check=list(HealthCheck),
)


def _assert_diverges(preset_id: str, num_cases: int, seed: int | None) -> None:
    bug = run_verification(preset_id, None, None, num_cases, seed, simulate_float_bug=True)
    assert bug.total_cases == num_cases
    assert bug.failed_cases > 0, f"float bug did not diverge: {preset_id} seed={seed}"
    assert bug.verified is False
    assert bug.passed_cases + bug.failed_cases == bug.total_cases
    assert 1 <= len(bug.counterexamples) <= MAX_COUNTEREXAMPLES
    for ce in bug.counterexamples:
        assert ce.penny_delta != "n/a"
        assert ce.penny_delta not in ("0", "0.0", "0.00")
        assert ce.likely_cause in ALLOWED_CAUSES
    assert any(ce.likely_cause == "float_drift" for ce in bug.counterexamples), (
        [ce.likely_cause for ce in bug.counterexamples])

    ref = run_verification(preset_id, None, None, num_cases, seed)
    assert ref.failed_cases == 0, ref.counterexamples
    assert ref.passed_cases == ref.total_cases == num_cases
    assert ref.verified is True
    assert ref.parity_score_pct == "100.00"


@pytest.mark.parametrize("preset_id", PRESETS)
def test_float_bug_diverges_default_run(preset_id: str) -> None:
    """Non-property: default 150-case run, no seed (Requirements 12.9)."""
    _assert_diverges(preset_id, 150, None)


@pytest.mark.parametrize("preset_id", PRESETS)
@PBT_SETTINGS
@given(num_cases=st.integers(150, 250), seed=st.integers(0, 2**32 - 1))
def test_float_bug_always_diverges(preset_id: str, num_cases: int, seed: int) -> None:
    # Feature: cobol-verification-engine, Property 13: The float-bug demonstration always diverges
    # **Validates: Requirements 12.9**
    _assert_diverges(preset_id, num_cases, seed)
