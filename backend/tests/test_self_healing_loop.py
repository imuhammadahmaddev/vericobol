"""Example tests for SelfHealingLoop with a stub LLM.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8**
"""

from __future__ import annotations

import pytest

from app import resurrection
from app.pbt_verifier import Counterexample, ParityReport, VerificationError
from app.resurrection import (
    ArchitectOutput,
    ParsedRules,
    PipelineStageError,
    SelfHealingLoop,
    contract_from_preset,
)

SOURCE = "       IDENTIFICATION DIVISION.\n"
RULES = ParsedRules(business_rules=["r"], pic_mappings=[])
SCHEMA = contract_from_preset("loan_calc")


def _cx(tag: str) -> Counterexample:
    return Counterexample(
        inputs={"principal": tag},
        cobol_output={"monthly_payment": "1.00"},
        python_output={"monthly_payment": "1.01"},
        differing_field="monthly_payment",
        penny_delta="0.01",
        likely_cause="float drift",
    )


def _report(passed: int, total: int = 150, cxs: list[Counterexample] | None = None) -> ParityReport:
    return ParityReport.build(passed, total - passed, 1.0, [], cxs or [])


class StubLLM:
    def __init__(self):
        self.repairs: list[tuple[Counterexample, str]] = []

    def repair_python(self, cobol_source, rules, schema, counterexample, previous_code):
        self.repairs.append((counterexample, previous_code))
        return ArchitectOutput(f"code{len(self.repairs) + 1}", {})


def _patch(monkeypatch, outcomes):
    """Replace run_verification with a scripted sequence; return the call log."""
    calls: list[dict] = []
    queue = list(outcomes)

    def fake(preset_id, cobol_source, **kw):
        calls.append({"preset_id": preset_id, "source": cobol_source, **kw})
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(resurrection, "run_verification", fake)
    return calls


def _run(llm):
    return SelfHealingLoop(llm).run(SOURCE, "loan_calc", RULES, SCHEMA, "code1", "llm", 150)


def test_first_attempt_passes_no_repair(monkeypatch):
    calls = _patch(monkeypatch, [_report(150)])
    llm = StubLLM()
    trail, final, code = _run(llm)
    assert len(calls) == 1 and llm.repairs == []
    assert [a.attempt for a in trail] == [1]
    assert trail[0].counterexample is None
    assert (final.certified, final.status, code) == (True, "passed", "code1")


def test_repair_uses_first_counterexample_then_certifies(monkeypatch):
    calls = _patch(monkeypatch, [_report(148, cxs=[_cx("a"), _cx("b")]), _report(150)])
    llm = StubLLM()
    trail, final, code = _run(llm)
    assert llm.repairs == [(_cx("a"), "code1")]
    assert [c["python_code"] for c in calls] == ["code1", "code2"]
    assert all(c["source"] == SOURCE and c["num_cases"] == 150 for c in calls)
    assert trail[0].counterexample == _cx("a") and trail[0].parity_score_pct != "100.00"
    assert trail[1].counterexample is None and trail[1].parity_score_pct == "100.00"
    assert (final.certified, final.status, code) == (True, "passed", "code2")


def test_failure_after_three_attempts(monkeypatch):
    calls = _patch(
        monkeypatch,
        [_report(100, cxs=[_cx("1")]), _report(110, cxs=[_cx("2")]), _report(120, cxs=[_cx("3")])],
    )
    llm = StubLLM()
    trail, final, code = _run(llm)
    assert len(calls) == 3 and len(llm.repairs) == 2 == SelfHealingLoop.MAX_RETRIES
    assert [a.attempt for a in trail] == [1, 2, 3]
    assert all(a.counterexample is not None for a in trail)
    assert [c.inputs["principal"] for c, _ in llm.repairs] == ["1", "2"]
    assert (final.certified, final.status, code) == (False, "failed", "code3")
    assert final.report.passed_cases == 120


@pytest.mark.parametrize("kind,custom,status", [
    ("rejected", False, 400),
    ("run_timeout", False, 504),
    ("compile_failed", True, 422),
    ("run_failed", False, 500),
    ("run_failed", True, 422),
])
def test_verification_error_propagates_without_retry(monkeypatch, kind, custom, status):
    calls = _patch(monkeypatch, [VerificationError(kind, "boom"), _report(150)])
    llm = StubLLM()
    source = SOURCE if custom else None
    with pytest.raises(PipelineStageError) as info:
        SelfHealingLoop(llm).run(source, "loan_calc", RULES, SCHEMA, "code1", "llm", 150)
    assert (info.value.status, info.value.stage) == (status, "parity_auditor")
    assert info.value.body == {"error": kind, "message": "boom"}
    assert len(calls) == 1 and llm.repairs == []


def test_error_on_retry_stops_loop(monkeypatch):
    calls = _patch(monkeypatch, [_report(100, cxs=[_cx("a")]), VerificationError("run_timeout", "t")])
    llm = StubLLM()
    with pytest.raises(PipelineStageError):
        _run(llm)
    assert len(calls) == 2 and len(llm.repairs) == 1
