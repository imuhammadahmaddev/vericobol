"""POST /api/resurrect: request validation and error mapping with a scripted LLM.

The LLM is a ``ScriptedLLM`` (no network, no tokens). The sandbox validator and
the compile step run for real, so a rejected source never reaches the LLM.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main
from app.llm_client import BudgetExceeded, LLMCallError, LLMClient, LLMKeyMissing
from app.pipeline_cache import PipelineCache
from app.resurrection import Pipeline

from .conftest import ScriptedLLM, preset_program, preset_source

PRESET_IDS = ["loan_calc", "compound_int", "daily_ledger"]


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def install(monkeypatch):
    def _install(llm) -> PipelineCache:
        cache = PipelineCache()
        monkeypatch.setattr(main, "_PIPELINE", Pipeline(llm, cache))
        return cache

    return _install


def _llm(**kw) -> ScriptedLLM:
    return ScriptedLLM(preset_program("loan_calc"), **kw)


def test_request_shape_errors_are_422_and_make_no_llm_call(client, install):
    llm = _llm()
    install(llm)
    code = preset_source("loan_calc")
    bad_bodies = [
        {},
        {"preset_id": "loan_calc", "cobol_code": code},
        {"preset_id": "loan_calc", "num_cases": 149},
        {"preset_id": "loan_calc", "num_cases": 201},
        {"preset_id": "loan_calc", "num_cases": "150"},
        {"cobol_code": code, "field_schema": []},  # removed request field
    ]
    for body in bad_bodies:
        assert client.post("/api/resurrect", json=body).status_code == 422, body
    assert client.post("/api/resurrect", content="{not json").status_code == 422
    assert llm.call_count == 0


def test_unknown_preset_is_404_with_valid_ids(client, install):
    llm = _llm()
    install(llm)
    r = client.post("/api/resurrect", json={"preset_id": "nope"})
    assert r.status_code == 404
    assert r.json()["detail"]["valid_ids"] == PRESET_IDS
    assert llm.call_count == 0


def test_body_over_256_kib_is_413(client, install):
    install(_llm())
    r = client.post(
        "/api/resurrect",
        content=b" " * (256 * 1024 + 1),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413


def test_sandbox_rejection_is_400_and_makes_no_llm_call(client, install):
    llm = _llm()
    install(llm)
    bad = preset_source("loan_calc") + '\n       CALL "SYSTEM" USING "ls".\n'
    r = client.post("/api/resurrect", json={"cobol_code": bad})
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "rejected"
    assert llm.call_count == 0


@pytest.mark.parametrize(
    "exc,cause", [(LLMKeyMissing(), "missing_key"), (BudgetExceeded(), "budget_exhausted")]
)
def test_unavailable_llm_is_503_with_cause_and_no_bundle_fallback(client, install, exc, cause):
    llm = _llm(unavailable=exc)
    install(llm)
    r = client.post("/api/resurrect", json={"preset_id": "loan_calc"})
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert detail["error"] == "llm_unavailable" and detail["cause"] == cause
    assert llm.call_count == 0


def test_real_client_without_key_is_503_missing_key(client, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setattr(main, "_PIPELINE", Pipeline(LLMClient.from_env(), PipelineCache()))
    r = client.post("/api/resurrect", json={"preset_id": "daily_ledger"})
    assert r.status_code == 503
    assert r.json()["detail"]["cause"] == "missing_key"
    assert "verified_python_code" not in r.text


def test_llm_call_failure_is_502_with_stage(client, install):
    install(_llm(raises=LLMCallError("legacy_parser", "boom")))
    r = client.post("/api/resurrect", json={"preset_id": "loan_calc"})
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert detail["error"] == "llm_call_failed" and detail["stage"] == "legacy_parser"


def test_health_does_not_build_pipeline(client, monkeypatch):
    monkeypatch.setattr(main, "_PIPELINE", None)
    assert client.get("/health").status_code == 200
    assert main._PIPELINE is None
