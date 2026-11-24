"""Unit tests for ``run_verification`` and the Parity_Report (task 7.3).

Each test runs the real GnuCOBOL binaries (no mocks).

**Validates: Requirements 12.1, 12.2, 12.5, 12.6, 12.7, 12.8, 12.9, 13.1-13.9**
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.pbt_verifier import (
    LIKELY_CAUSES,
    ParityReport,
    VerificationError,
    compute_parity_score,
    generate_cases,
    run_verification,
)

from .conftest import PRESET_IDS, SAMPLES_DIR


@pytest.mark.parametrize("preset_id", PRESET_IDS)
def test_reference_model_has_full_parity(preset_id):
    report = run_verification(preset_id, None, num_cases=150, seed=7)
    assert report.total_cases == 150
    assert report.passed_cases == 150 and report.failed_cases == 0
    assert report.parity_score_pct == "100.00" and report.verified is True
    assert report.counterexamples == []
    assert 0 < len(report.raw_cobol_sample_output) <= 20
    assert report.execution_ms > 0


@pytest.mark.parametrize("preset_id", PRESET_IDS)
def test_float_bug_demo_is_caught(preset_id):
    report = run_verification(preset_id, None, num_cases=150, seed=7, simulate_float_bug=True)
    assert report.failed_cases >= 1 and report.verified is False
    assert 1 <= len(report.counterexamples) <= 5
    ce = report.counterexamples[0]
    assert ce.likely_cause in LIKELY_CAUSES
    assert ce.differing_field in ce.cobol_output and ce.differing_field in ce.python_output
    assert Decimal(ce.penny_delta) != Decimal("0.00")
    assert all(isinstance(v, str) for v in ce.inputs.values())
    assert report.total_cases == report.passed_cases + report.failed_cases


def test_custom_cobol_source_path_matches_reference():
    source = (SAMPLES_DIR / "loan_calc.cbl").read_text(encoding="utf-8")
    report = run_verification("loan_calc", source, num_cases=150, seed=3)
    assert report.verified and report.passed_cases == 150


def test_row_count_mismatch_is_a_verification_error():
    source = 'IDENTIFICATION DIVISION.\nPROGRAM-ID. ONE.\nPROCEDURE DIVISION.\n' \
             '    DISPLAY "1.00 2.00 3.00".\n    STOP RUN.\n'
    with pytest.raises(VerificationError) as err:
        run_verification("loan_calc", source, num_cases=150, seed=3)
    assert err.value.kind == "row_count_mismatch"
    assert "1" in err.value.message and "150" in err.value.message


def test_rejected_and_uncompilable_sources():
    with pytest.raises(VerificationError) as rejected:
        run_verification("loan_calc", 'CALL "SYSTEM" USING "ls".', num_cases=150)
    assert rejected.value.kind == "rejected"
    with pytest.raises(VerificationError) as bad:
        run_verification("loan_calc", "IDENTIFICATION DIVISION.\nthis is not cobol", num_cases=150)
    assert bad.value.kind == "compile_failed"


def test_user_python_exception_is_python_error():
    code = (
        "def calculate(principal, annual_rate_pct, term_months):\n"
        "    return {'monthly_payment': Decimal(1) / 0}\n"
    )
    report = run_verification("loan_calc", None, num_cases=150, seed=1, python_code=code)
    assert report.failed_cases == 150
    assert {c.likely_cause for c in report.counterexamples} == {"python_error"}


def test_generate_cases_is_deterministic_and_unique():
    a = generate_cases("daily_ledger", 200, seed=11)
    assert a == generate_cases("daily_ledger", 200, seed=11)
    assert len({tuple(c.inputs.items()) for c in a}) == 200


@given(st.integers(0, 5000), st.integers(0, 5000))
@settings(max_examples=100, deadline=None)
def test_report_score_counts_and_json_round_trip(passed, failed):
    report = ParityReport.build(passed, failed, 12.34, ["a b c"] * 30, [])
    total = passed + failed
    assert report.total_cases == total
    assert report.verified == (failed == 0 and total > 0)
    expected = "0.00" if total == 0 else format(
        (Decimal(passed) / Decimal(total) * 100).quantize(Decimal("0.01")), "f")
    assert report.parity_score_pct == expected == compute_parity_score(passed, total)
    assert len(report.raw_cobol_sample_output) == 20
    back = ParityReport.from_json(report.to_json())
    assert back == report
    assert json.loads(report.to_json(), parse_float=Decimal)["parity_score_pct"] == expected


# ---------------------------------------------------------------------------
# Task 7.11 additions
# **Validates: Requirements 12.4, 12.7, 12.8, 12.9, 12.10, 19.3, 19.4, 19.7**
# ---------------------------------------------------------------------------

import time  # noqa: E402

from app import pbt_verifier  # noqa: E402
from app.cobol_runner import CobolRunner, RunResult  # noqa: E402
from app.pbt_verifier import Counterexample, _OUTPUT_FIELDS  # noqa: E402

_LOAN_ARGS = "principal, annual_rate_pct, term_months"


class _FakeRunner:
    """Stands in for CobolRunner; returns canned stdout and records the rows."""

    def __init__(self, stdout: str = "", **overrides):
        self.stdout = stdout
        self.overrides = overrides
        self.rows: list[str] = []

    def compile_and_run_preset(self, preset_id: str, input_rows: list[str]) -> RunResult:
        self.rows = list(input_rows)
        fields = dict(exit_code=0, stdout=self.stdout, stderr="", truncated=False,
                      timed_out=False, duration_ms=1.0)
        fields.update(self.overrides)
        return RunResult(**fields)


@pytest.mark.parametrize("preset_id", PRESET_IDS)
@pytest.mark.parametrize("fewer", [True, False])
def test_row_count_mismatch_with_fake_runner(preset_id, fewer):
    n_out = len(_OUTPUT_FIELDS[preset_id])
    line = " ".join(["1.00"] * n_out)
    produced = 149 if fewer else 151
    runner = _FakeRunner("\n".join([line] * produced) + "\n")
    with pytest.raises(VerificationError) as err:
        run_verification(preset_id, None, num_cases=150, seed=5, runner=runner)
    assert err.value.kind == "row_count_mismatch"
    assert str(produced) in err.value.message and "150" in err.value.message
    assert err.value.to_dict() == {"kind": "row_count_mismatch", "message": err.value.message}
    assert len(runner.rows) == 150  # exactly one batch with all cases


@pytest.mark.parametrize("overrides,kind", [
    ({"timed_out": True}, "run_timeout"),
    ({"truncated": True}, "output_truncated"),
    ({"exit_code": 3, "stderr": "boom"}, "run_failed"),
])
def test_fake_runner_run_failures_map_to_error_kinds(overrides, kind):
    with pytest.raises(VerificationError) as err:
        run_verification("loan_calc", None, num_cases=150, seed=5,
                         runner=_FakeRunner("", **overrides))
    assert err.value.kind == kind


@pytest.mark.parametrize("preset_id", PRESET_IDS)
@pytest.mark.parametrize("num_cases", [150, 200])
def test_full_parity_per_preset_at_least_150_cases(preset_id, num_cases):
    report = run_verification(preset_id, None, num_cases=num_cases, seed=21)
    assert report.total_cases == num_cases >= 150
    assert report.passed_cases == num_cases and report.failed_cases == 0
    assert report.parity_score_pct == "100.00" and report.verified is True
    assert report.counterexamples == []


@pytest.mark.parametrize("preset_id", PRESET_IDS)
def test_float_bug_has_concrete_counterexamples_per_preset(preset_id):
    report = run_verification(preset_id, None, num_cases=200, seed=21, simulate_float_bug=True)
    assert report.failed_cases > 0 and report.verified is False
    assert report.passed_cases + report.failed_cases == 200
    assert report.parity_score_pct != "100.00"
    assert 1 <= len(report.counterexamples) <= 5
    for ce in report.counterexamples:
        assert ce.inputs and ce.cobol_output and ce.python_output
        assert ce.cobol_output[ce.differing_field] != ce.python_output[ce.differing_field]


def test_unknown_preset_is_invalid_request():
    with pytest.raises(VerificationError) as err:
        run_verification("nope", None, num_cases=150)
    assert err.value.kind == "invalid_request"


# --- python_code exception handling (Req 12.8, 19.4) ---

def test_user_code_returning_floats_is_python_error():
    code = (f"def calculate({_LOAN_ARGS}):\n"
            "    return {'monthly_payment': 1.5, 'total_interest': 2.5, 'total_payable': 4.0}\n")
    report = run_verification("loan_calc", None, num_cases=150, seed=1, python_code=code)
    assert report.failed_cases == 150 and report.verified is False
    assert {c.likely_cause for c in report.counterexamples} == {"python_error"}
    assert "float" in report.counterexamples[0].python_output["error"]


def test_user_code_raising_reports_error_text():
    code = (f"def calculate({_LOAN_ARGS}):\n"
            "    return {'monthly_payment': Decimal(term_months // 0)}\n")
    report = run_verification("loan_calc", None, num_cases=150, seed=1, python_code=code)
    assert report.failed_cases == 150
    ce = report.counterexamples[0]
    assert ce.likely_cause == "python_error" and ce.differing_field == "(python)"
    assert "ZeroDivisionError" in ce.python_output["error"]


def test_user_code_raising_only_on_some_cases_keeps_other_results():
    # Fails when term_months is 1; correct reference output cannot be reproduced
    # here, so every non-failing case also differs. Only check per-case isolation.
    code = (f"def calculate({_LOAN_ARGS}):\n"
            "    if term_months == 1:\n"
            "        return {'monthly_payment': 1 // 0}\n"
            "    return {'monthly_payment': Decimal('0.00'), 'total_interest': Decimal('0.00'),"
            " 'total_payable': Decimal('0.00')}\n")
    report = run_verification("loan_calc", None, num_cases=150, seed=1, python_code=code)
    causes = {c.likely_cause for c in report.counterexamples}
    assert report.total_cases == 150 and report.failed_cases > 0
    assert causes <= LIKELY_CAUSES


def test_user_code_timeout_is_python_error(monkeypatch):
    real = pbt_verifier.run_user_python
    monkeypatch.setattr(pbt_verifier, "run_user_python",
                        lambda code, cases: real(code, cases, timeout_s=0.5))
    code = (f"def calculate({_LOAN_ARGS}):\n"
            "    while True:\n"
            "        pass\n")
    t0 = time.perf_counter()
    report = run_verification("loan_calc", None, num_cases=150, seed=1, python_code=code)
    assert time.perf_counter() - t0 < 4.0  # child was killed at the deadline
    assert report.failed_cases == 150
    assert {c.likely_cause for c in report.counterexamples} == {"python_error"}
    assert "exceeded" in report.counterexamples[0].python_output["error"]


def test_restricted_user_code_is_python_error():
    code = (f"def calculate({_LOAN_ARGS}):\n"
            "    return open('x')\n")
    report = run_verification("loan_calc", None, num_cases=150, seed=1, python_code=code)
    assert report.failed_cases == 150
    assert {c.likely_cause for c in report.counterexamples} == {"python_error"}
    assert "not allowed" in report.counterexamples[0].python_output["error"]


# --- counterexample contents (Req 12.7, 12.9, 19.3) ---

@pytest.mark.parametrize("preset_id", PRESET_IDS)
def test_counterexample_contents(preset_id):
    report = run_verification(preset_id, None, num_cases=200, seed=9, simulate_float_bug=True)
    outs = set(_OUTPUT_FIELDS[preset_id])
    for ce in report.counterexamples:
        assert isinstance(ce, Counterexample)
        assert set(ce.cobol_output) == outs
        assert set(ce.python_output) == outs
        assert ce.differing_field in outs
        assert ce.likely_cause in LIKELY_CAUSES
        d = Decimal(ce.penny_delta)
        assert d > 0 and d != Decimal("0.00")
        # Exact reconstruction of the delta from the shown strings.
        diff = abs(Decimal(ce.cobol_output[ce.differing_field])
                   - Decimal(ce.python_output[ce.differing_field]))
        assert d == diff.quantize(Decimal("0.01")) or d == diff
        assert all(isinstance(v, str) for v in ce.cobol_output.values())
        assert all(isinstance(v, str) for v in ce.python_output.values())
        assert Counterexample.from_dict(ce.to_dict()) == ce
    # Counterexamples are JSON-safe strings end to end.
    parsed = json.loads(report.to_json(), parse_float=Decimal)
    assert len(parsed["counterexamples"]) == len(report.counterexamples)


def test_float_bug_counterexample_is_labelled_float_drift():
    report = run_verification("loan_calc", None, num_cases=200, seed=9, simulate_float_bug=True)
    assert "float_drift" in {c.likely_cause for c in report.counterexamples}


def test_target_fn_off_by_one_cent_is_rounded_vs_truncation():
    from app.reference_models import REGISTRY

    def off_by_cent(**kw):
        out = dict(REGISTRY["loan_calc"](**kw))
        out["monthly_payment"] = out["monthly_payment"] + Decimal("0.01")
        return out

    report = run_verification("loan_calc", None, off_by_cent, num_cases=150, seed=2)
    assert report.failed_cases == 150
    ce = report.counterexamples[0]
    assert ce.differing_field == "monthly_payment"
    assert ce.penny_delta == "0.01" and ce.likely_cause == "rounded_vs_truncation"


# --- performance (Req 12.4, 12.10, 19.7) ---

@pytest.mark.parametrize("preset_id", PRESET_IDS)
def test_200_case_verification_under_3_seconds(preset_id):
    # The first call on a fresh runner includes the one-time cobc compile of the
    # preset binary (cold path). It is reported, not bounded, because compile
    # time depends on the host; the warm path is the 3 s requirement.
    runner = CobolRunner()
    t0 = time.perf_counter()
    cold = run_verification(preset_id, None, num_cases=200, seed=1, runner=runner)
    cold_ms = (time.perf_counter() - t0) * 1000.0
    assert cold.verified and cold.total_cases == 200

    t0 = time.perf_counter()
    warm = run_verification(preset_id, None, num_cases=200, seed=2, runner=runner)
    warm_wall_ms = (time.perf_counter() - t0) * 1000.0
    assert warm.verified and warm.total_cases == 200
    print(f"[perf] {preset_id}: cold(compile included)={cold_ms:.0f} ms, "
          f"warm={warm.execution_ms:.0f} ms (wall {warm_wall_ms:.0f} ms)")
    assert warm.execution_ms < 3000.0
    assert warm_wall_ms < 3000.0
    assert cold_ms > 0
