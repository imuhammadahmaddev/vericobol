"""Pipeline tests with a scripted LLM and real GnuCOBOL (task 5.1).

Needs ``cobc`` and ``fork``; run in the Docker image. No network, no tokens.
"""

from __future__ import annotations

import multiprocessing
import shutil
from pathlib import Path

import pytest

from app.llm_client import InferredProgram, LLMUnavailable
from app.pipeline_cache import PipelineCache
from app.resurrection import ArchitectOutput, Pipeline, PipelineStageError, find_float_construct

SOURCE = (Path(__file__).resolve().parent.parent / "cobol_samples" / "daily_ledger.cbl").read_text(
    encoding="utf-8"
)

needs_runtime = pytest.mark.skipif(
    shutil.which("cobc") is None or "fork" not in multiprocessing.get_all_start_methods(),
    reason="needs cobc and fork (use the Docker image)",
)

_IN = [
    ("opening_balance", "S9(9)V99"), ("deposits", "9(9)V99"), ("withdrawals", "9(9)V99"),
    ("overdraft_threshold", "S9(9)V99"), ("overdraft_fee", "9(5)V99"),
]
_OUT = [("closing_balance", "S9(9)V99"), ("fee_applied", "9(5)V99"), ("overdraft_flag", "9(1)")]

PROGRAM = InferredProgram(
    program_title="Daily Ledger",
    description="Reconciles one day.",
    io_protocol="batch_space",
    contract={
        "inputs": [
            {"name": n, "label": n, "pic": p, "scale": 2,
             "min": "-1000000.00" if p.startswith("S") else "0.00", "max": "1000000.00", "step": None}
            for n, p in _IN
        ],
        "outputs": [
            {"name": n, "label": n, "pic": p, "scale": 0 if n == "overdraft_flag" else 2,
             "rounding": "ROUND_DOWN", "format": "flag" if n == "overdraft_flag" else "currency"}
            for n, p in _OUT
        ],
    },
    business_rules=["A fee applies below the threshold."],
    dashboard_metadata={"title": "Ledger", "chart_type": "summary", "inputs": [], "outputs": []},
)

DECIMAL_CODE = """
def calculate(**kw):
    bal = Decimal(kw["opening_balance"]) + Decimal(kw["deposits"]) - Decimal(kw["withdrawals"])
    low = bal < Decimal(kw["overdraft_threshold"])
    fee = Decimal(kw["overdraft_fee"]) if low else Decimal("0.00")
    return {"closing_balance": bal - fee, "fee_applied": fee, "overdraft_flag": 1 if low else 0}
"""

FLOAT_CODE = """
def calculate(**kw):
    bal = float(kw["opening_balance"]) + float(kw["deposits"]) - float(kw["withdrawals"])
    low = bal < float(kw["overdraft_threshold"])
    fee = float(kw["overdraft_fee"]) if low else 0.0
    return {"closing_balance": bal - fee, "fee_applied": fee, "overdraft_flag": 1 if low else 0}
"""


class FakeLLM:
    def __init__(self, unavailable: bool = False):
        self.unavailable = unavailable
        self.calls = {"infer": 0, "generate": 0, "repair": 0}

    def require_key(self):
        if self.unavailable:
            raise LLMUnavailable("missing_key")

    def infer_contract(self, source, validation_error=None):
        self.calls["infer"] += 1
        return PROGRAM

    def generate_python(self, source, program, *, float_demo=False):
        self.calls["generate"] += 1
        return ArchitectOutput(FLOAT_CODE if float_demo else DECIMAL_CODE, {})

    def repair_python(self, source, program, counterexample, previous_code):
        self.calls["repair"] += 1
        return ArchitectOutput(DECIMAL_CODE, {})


def test_float_guard_detects_constructs():
    assert find_float_construct("x = 1.5") is not None
    assert find_float_construct("y = float(1)") is not None
    assert find_float_construct("from decimal import Decimal\nx = Decimal('1.5')") is None


def test_sandbox_rejection_makes_no_llm_call():
    llm = FakeLLM()
    with pytest.raises(PipelineStageError) as ei:
        Pipeline(llm, PipelineCache()).run('IDENTIFICATION DIVISION.\nCALL "SYSTEM" USING "ls".')
    assert ei.value.status == 400
    assert llm.calls == {"infer": 0, "generate": 0, "repair": 0}


def test_unavailable_llm_returns_503_without_fallback():
    with pytest.raises(PipelineStageError) as ei:
        Pipeline(FakeLLM(unavailable=True), PipelineCache()).run(SOURCE)
    assert ei.value.status in (503, 400)  # 400 only when cobc is missing on this host
    if ei.value.status == 503:
        assert ei.value.body["cause"] == "missing_key"


@needs_runtime
def test_live_run_certifies_then_cache_hit_still_verifies_live():
    llm, cache = FakeLLM(), PipelineCache()
    first = Pipeline(llm, cache).run(SOURCE)
    assert first.certified and not first.cache_hit
    assert [s.source for s in first.stages] == ["llm", "llm", "live"]
    assert len(cache) == 1
    second = Pipeline(llm, cache).run(SOURCE)
    assert second.cache_hit and second.certified
    assert llm.calls == {"infer": 1, "generate": 1, "repair": 0}
    assert second.stages[2].source == "live"
    assert second.final_report.report.total_cases >= 1


@needs_runtime
def test_self_healing_demo_repairs_float_drift():
    llm = FakeLLM()
    resp = Pipeline(llm, PipelineCache()).run(SOURCE, demonstrate_self_healing=True)
    assert resp.certified
    assert len(resp.audit_trail) == 2
    assert resp.audit_trail[0].counterexample is not None
    assert resp.audit_trail[0].counterexample.likely_cause == "float_drift"
    assert llm.calls["repair"] == 1
    assert find_float_construct(resp.verified_python_code) is None
