"""End-to-end tests of POST /api/resurrect with a scripted LLM and real GnuCOBOL.

No network and no LLM tokens. ``preset_bundles.json`` supplies the Decimal
Python that a good LLM would return (a test-only oracle); the real ``cobc``
binary is the judge. Only Certified live-LLM results enter the cache, and a
cache hit still runs a live verification.
"""

from __future__ import annotations

import ast

import pytest
from fastapi.testclient import TestClient

from app import main
from app.llm_client import InferredProgram
from app.pipeline_cache import PipelineCache, cache_key
from app.resurrection import Pipeline, find_float_construct

from .conftest import PRESET_IDS, ScriptedLLM, oracle_python, preset_program, preset_source

NUM_CASES = 150
STAGE_NAMES = ["legacy_parser", "cloud_architect", "parity_auditor"]
RESPONSE_KEYS = {
    "preset_id", "cobol_code", "program_title", "description", "io_protocol", "contract",
    "cache_hit", "llm_used", "stages", "business_rules", "pic_mappings",
    "dashboard_metadata", "verified_python_code", "export_fastapi_code",
    "audit_trail", "parity_report", "final_report", "certified",
}

# Naive IEEE-754 translation of daily_ledger.cbl (the float-drift demo input).
FLOAT_LEDGER = """
def calculate(**kw):
    bal = float(kw["opening_balance"]) + float(kw["deposits"]) - float(kw["withdrawals"])
    low = bal < float(kw["overdraft_threshold"])
    fee = float(kw["overdraft_fee"]) if low else 0.0
    return {"closing_balance": bal - fee, "fee_applied": fee, "overdraft_flag": 1 if low else 0}
"""


def _truncating_loan_code() -> str:
    """Oracle loan code with ROUNDED replaced by truncation (a classic bug)."""
    good = oracle_python("loan_calc")
    bad = good.replace("_round_half_up(q, qs, 2)", "(q // 10 ** (qs - 2))")
    assert bad != good
    return bad


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def install(monkeypatch):
    """``install(llm) -> PipelineCache``: swap the process-wide pipeline."""

    def _install(llm) -> PipelineCache:
        cache = PipelineCache()
        monkeypatch.setattr(main, "_PIPELINE", Pipeline(llm, cache))
        return cache

    return _install


def _resurrect(client, **payload):
    return client.post("/api/resurrect", json=payload)


def _live_llm(preset_id: str, **kw) -> ScriptedLLM:
    return ScriptedLLM(preset_program(preset_id), generated=oracle_python(preset_id), **kw)


# --------------------------------------------------------------------------
# Presets run through the live pipeline
# --------------------------------------------------------------------------

@pytest.mark.parametrize("preset_id", PRESET_IDS)
def test_preset_runs_live_pipeline_and_certifies(client, install, preset_id):
    llm = _live_llm(preset_id)
    install(llm)
    r = _resurrect(client, preset_id=preset_id)
    assert r.status_code == 200, r.text
    body = r.json()

    assert set(body) == RESPONSE_KEYS
    assert body["preset_id"] == preset_id
    assert body["cobol_code"] == preset_source(preset_id)
    assert body["cache_hit"] is False and body["llm_used"] is True
    assert [s["name"] for s in body["stages"]] == STAGE_NAMES
    assert [s["source"] for s in body["stages"]] == ["llm", "llm", "live"]
    assert body["certified"] is True

    final = body["final_report"]
    assert final["status"] == "passed" and final["verified"] is True
    assert final["parity_score_pct"] == "100.00"
    assert final["total_cases"] == final["passed_cases"] == NUM_CASES
    assert final["counterexamples"] == []
    assert [a["attempt"] for a in body["audit_trail"]] == [1]
    assert body["audit_trail"][0]["counterexample"] is None
    assert llm.calls == {"infer": 1, "generate": 1, "repair": 0}

    assert find_float_construct(body["verified_python_code"]) is None
    ast.parse(body["export_fastapi_code"])
    names = [i["name"] for i in body["dashboard_metadata"]["inputs"]]
    assert names == [f["name"] for f in preset_program(preset_id).contract["inputs"]]


def test_pasted_cobol_has_no_preset_id(client, install):
    install(_live_llm("loan_calc"))
    r = _resurrect(client, cobol_code=preset_source("loan_calc"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["preset_id"] is None and body["certified"] is True


# --------------------------------------------------------------------------
# Self-healing
# --------------------------------------------------------------------------

def test_float_drift_demo_fails_then_repairs(client, install):
    llm = ScriptedLLM(
        preset_program("daily_ledger"),
        generated=FLOAT_LEDGER,
        repaired=oracle_python("daily_ledger"),
    )
    install(llm)
    r = _resurrect(client, preset_id="daily_ledger", demonstrate_self_healing=True)
    assert r.status_code == 200, r.text
    body = r.json()

    trail = body["audit_trail"]
    assert [a["attempt"] for a in trail] == [1, 2]
    first = trail[0]
    assert first["passed_cases"] < first["total_cases"] and first["parity_score_pct"] != "100.00"
    cx = first["counterexample"]
    assert set(cx) == {
        "inputs", "cobol_output", "python_output",
        "differing_field", "penny_delta", "likely_cause",
    }
    assert cx["likely_cause"] == "float_drift"
    assert cx["cobol_output"][cx["differing_field"]] != cx["python_output"][cx["differing_field"]]
    assert trail[1]["parity_score_pct"] == "100.00" and trail[1]["counterexample"] is None
    assert body["certified"] is True
    assert llm.calls["generate"] == 1 and llm.calls["repair"] == 1
    assert llm.call_log[0][0] == "infer"
    assert ("generate", True) == (llm.call_log[1][0], llm.call_log[1][1][2])  # float_demo flag
    assert find_float_construct(body["verified_python_code"]) is None


def test_truncation_bug_is_repaired_from_first_counterexample(client, install):
    llm = ScriptedLLM(
        preset_program("loan_calc"),
        generated=_truncating_loan_code(),
        repaired=oracle_python("loan_calc"),
    )
    install(llm)
    r = _resurrect(client, preset_id="loan_calc")
    assert r.status_code == 200, r.text
    body = r.json()

    trail = body["audit_trail"]
    assert len(trail) == 2 and trail[0]["counterexample"] is not None
    assert trail[1]["parity_score_pct"] == "100.00"
    assert body["certified"] is True
    name, args = llm.call_log[-1]
    assert name == "repair"
    assert args[2].differing_field == trail[0]["counterexample"]["differing_field"]
    assert args[3] == _truncating_loan_code()  # previous code is passed to the repair


def test_always_wrong_llm_fails_after_two_retries_with_http_200(client, install):
    bad = _truncating_loan_code()
    llm = ScriptedLLM(preset_program("loan_calc"), generated=bad, repaired=bad)
    cache = install(llm)
    r = _resurrect(client, preset_id="loan_calc")
    assert r.status_code == 200, r.text
    body = r.json()

    assert [a["attempt"] for a in body["audit_trail"]] == [1, 2, 3]
    assert all(a["counterexample"] is not None for a in body["audit_trail"])
    assert body["certified"] is False and body["final_report"]["status"] == "failed"
    assert body["final_report"]["counterexamples"]
    assert body["stages"][2]["name"] == "parity_auditor" and body["stages"][2]["status"] == "failed"
    assert llm.calls["repair"] == 2
    assert len(cache) == 0  # uncertified results are never cached


def test_float_code_outside_the_demo_counts_as_failed_attempt(client, install):
    llm = ScriptedLLM(
        preset_program("daily_ledger"),
        generated=FLOAT_LEDGER,
        repaired=oracle_python("daily_ledger"),
    )
    install(llm)
    r = _resurrect(client, preset_id="daily_ledger")  # demo flag off
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["audit_trail"]) == 2
    assert body["audit_trail"][0]["counterexample"]["python_output"]["error"].startswith("float")
    assert body["certified"] is True


# --------------------------------------------------------------------------
# Pipeline_Cache
# --------------------------------------------------------------------------

def test_certified_result_is_cached_and_hit_still_verifies_live(client, install):
    llm = _live_llm("loan_calc")
    cache = install(llm)
    key = cache_key(preset_source("loan_calc"), False)
    assert len(cache) == 0 and key not in cache  # starts empty, nothing seeded

    first = _resurrect(client, preset_id="loan_calc").json()
    assert first["cache_hit"] is False and key in cache
    assert llm.calls == {"infer": 1, "generate": 1, "repair": 0}

    second = _resurrect(client, preset_id="loan_calc").json()
    assert second["cache_hit"] is True and second["llm_used"] is False
    assert llm.calls == {"infer": 1, "generate": 1, "repair": 0}  # 0 extra tokens
    assert [s["source"] for s in second["stages"]] == ["cache", "cache", "live"]
    assert second["certified"] is True
    assert second["final_report"]["total_cases"] == NUM_CASES
    assert second["verified_python_code"] == first["verified_python_code"]


def test_force_live_llm_bypasses_cache(client, install):
    llm = _live_llm("loan_calc")
    install(llm)
    _resurrect(client, preset_id="loan_calc")
    again = _resurrect(client, preset_id="loan_calc", force_live_llm=True).json()
    assert again["cache_hit"] is False and again["llm_used"] is True
    assert llm.calls["infer"] == 2 and llm.calls["generate"] == 2


def test_demo_flag_uses_a_separate_cache_entry():
    src = preset_source("loan_calc")
    assert cache_key(src, True) != cache_key(src, False)


# --------------------------------------------------------------------------
# Contract problems
# --------------------------------------------------------------------------

def test_incomplete_contract_is_422_and_skips_the_architect(client, install):
    program = preset_program("loan_calc")
    empty = InferredProgram(
        program_title=program.program_title,
        description=program.description,
        io_protocol=program.io_protocol,
        contract={"inputs": program.contract["inputs"], "outputs": []},
        business_rules=program.business_rules,
        dashboard_metadata=program.dashboard_metadata,
    )
    llm = ScriptedLLM(empty, generated=oracle_python("loan_calc"))
    install(llm)
    r = _resurrect(client, preset_id="loan_calc")
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "incomplete_contract"
    assert llm.calls["generate"] == 0


def test_oracle_code_is_float_free():
    for preset_id in PRESET_IDS:
        assert find_float_construct(oracle_python(preset_id)) is None, preset_id
