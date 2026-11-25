"""SelfHealingLoop with a scripted verifier and LLM (no cobc needed).

Covers: stop at the first certification, repair from the first Counterexample,
at most 2 retries, no retry on a VerificationError, and float code that is
rejected before verification outside the float demo.
"""

from __future__ import annotations

import pytest

from app import resurrection
from app.pbt_verifier import Counterexample, ParityReport, VerificationError
from app.resurrection import ArchitectOutput, PipelineStageError, SelfHealingLoop

from .conftest import preset_program

SOURCE = "       IDENTIFICATION DIVISION.\n"
PROGRAM = preset_program("loan_calc")


def _cx(tag: str) -> Counterexample:
    return Counterexample(
        inputs={"principal": tag},
        cobol_output={"monthly_payment": "1.00"},
        python_output={"monthly_payment": "1.01"},
        differing_field="monthly_payment",
        penny_delta="0.01",
        likely_cause="float_drift",
    )


def _report(passed: int, total: int = 150, cxs: list[Counterexample] | None = None) -> ParityReport:
    return ParityReport.build(passed, total - passed, 1, [], cxs or [])


class StubLLM:
    def __init__(self):
        self.repairs: list[tuple[Counterexample, str]] = []
        self.generated = 0

    def repair_python(self, cobol_source, program, counterexample, previous_code):
        self.repairs.append((counterexample, previous_code))
        return ArchitectOutput(f"code{len(self.repairs) + 1}", {})

    def generate_python(self, cobol_source, program, *, float_demo=False):
        self.generated += 1
        return ArchitectOutput("exact_code", {})


def _patch(monkeypatch, outcomes):
    """Replace run_universal_verification with a scripted sequence; return the call log."""
    calls: list[dict] = []
    queue = list(outcomes)

    def fake(cobol_source, contract, io_protocol, python_code, **kw):
        calls.append({"source": cobol_source, "protocol": io_protocol, "code": python_code, **kw})
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(resurrection, "run_universal_verification", fake)
    return calls


def _run(llm, first_code="code1", **kw):
    return SelfHealingLoop(llm).run(SOURCE, PROGRAM, first_code, 150, **kw)


def test_first_attempt_passes_no_repair(monkeypatch):
    calls = _patch(monkeypatch, [_report(150)])
    llm = StubLLM()
    trail, final, code = _run(llm)
    assert len(calls) == 1 and llm.repairs == []
    assert calls[0]["protocol"] == PROGRAM.io_protocol and calls[0]["num_cases"] == 150
    assert trail[0].counterexample is None
    assert (final.certified, final.status, code) == (True, "passed", "code1")


def test_repair_uses_first_counterexample_then_certifies(monkeypatch):
    calls = _patch(monkeypatch, [_report(148, cxs=[_cx("a"), _cx("b")]), _report(150)])
    llm = StubLLM()
    trail, final, code = _run(llm)
    assert llm.repairs == [(_cx("a"), "code1")]
    assert [c["code"] for c in calls] == ["code1", "code2"]
    assert trail[0].counterexample == _cx("a") and trail[0].parity_score_pct != "100.00"
    assert trail[1].counterexample is None and trail[1].parity_score_pct == "100.00"
    assert (final.certified, final.status, code) == (True, "passed", "code2")


def test_failure_after_two_retries(monkeypatch):
    calls = _patch(
        monkeypatch,
        [_report(100, cxs=[_cx("1")]), _report(110, cxs=[_cx("2")]), _report(120, cxs=[_cx("3")])],
    )
    llm = StubLLM()
    trail, final, code = _run(llm)
    assert len(calls) == 3 and len(llm.repairs) == 2 == SelfHealingLoop.MAX_RETRIES
    assert [a.attempt for a in trail] == [1, 2, 3]
    assert [c.inputs["principal"] for c, _ in llm.repairs] == ["1", "2"]
    assert (final.certified, final.status, code) == (False, "failed", "code3")
    assert final.report.passed_cases == 120


@pytest.mark.parametrize("kind,status", [
    ("rejected", 400),
    ("compile_failed", 400),
    ("run_timeout", 504),
    ("invalid_request", 422),
])
def test_verification_error_propagates_without_retry(monkeypatch, kind, status):
    calls = _patch(monkeypatch, [VerificationError(kind, "boom"), _report(150)])
    llm = StubLLM()
    with pytest.raises(PipelineStageError) as info:
        _run(llm)
    assert (info.value.status, info.value.stage) == (status, "parity_auditor")
    assert info.value.body == {"error": kind, "message": "boom"}
    assert len(calls) == 1 and llm.repairs == []


def test_error_on_retry_stops_loop(monkeypatch):
    calls = _patch(monkeypatch, [_report(100, cxs=[_cx("a")]), VerificationError("run_timeout", "t")])
    llm = StubLLM()
    with pytest.raises(PipelineStageError):
        _run(llm)
    assert len(calls) == 2 and len(llm.repairs) == 1


def test_float_code_is_not_verified_and_counts_as_an_attempt(monkeypatch):
    calls = _patch(monkeypatch, [_report(150)])
    llm = StubLLM()
    trail, final, code = _run(llm, first_code="def calculate(**kw):\n    return {'x': float(1)}\n")
    assert len(calls) == 1  # only the repaired code reaches the verifier
    assert [a.attempt for a in trail] == [1, 2]
    assert trail[0].counterexample is not None and trail[0].counterexample.differing_field == "python_code"
    assert (final.certified, code) == (True, "code2")


def test_float_demo_attempt_is_verified_with_floats_allowed(monkeypatch):
    calls = _patch(monkeypatch, [_report(120, cxs=[_cx("d")]), _report(150)])
    llm = StubLLM()
    trail, final, _ = _run(llm, first_code="float_code", first_attempt_is_float_demo=True)
    assert [c["allow_float_demo"] for c in calls] == [True, False]
    assert final.certified and len(trail) == 2


def test_matching_float_demo_still_needs_exact_code(monkeypatch):
    _patch(monkeypatch, [_report(150), _report(150)])
    llm = StubLLM()
    trail, final, code = _run(llm, first_code="float_code", first_attempt_is_float_demo=True)
    assert llm.generated == 1 and llm.repairs == []
    assert (final.certified, code) == (True, "exact_code")
