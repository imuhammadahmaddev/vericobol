"""Smoke tests for POST /api/resurrect and GET /api/export/{preset_id}."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main
from app.llm_client import BudgetExceeded, LLMCallError, LLMKeyMissing
from app.pipeline_cache import PipelineCache
from app.resurrection import ArchitectOutput, ParsedRules, Pipeline, reference_decimal_source

from .conftest import SAMPLES_DIR, StubLLMClient

LOAN_SCHEMA = [
    {"name": "principal", "pic": "9(9)V99", "direction": "input"},
    {"name": "annual_rate_pct", "pic": "9(3)V999", "direction": "input"},
    {"name": "term_months", "pic": "9(3)", "direction": "input"},
    {"name": "monthly_payment", "pic": "9(9)V99", "direction": "output", "rounding": "ROUND_HALF_UP"},
    {"name": "total_interest", "pic": "9(9)V99", "direction": "output", "rounding": "ROUND_HALF_UP"},
    {"name": "total_payable", "pic": "9(9)V99", "direction": "output", "rounding": "ROUND_HALF_UP"},
]


@pytest.fixture
def client():
    return TestClient(main.app)


def _install(monkeypatch, llm) -> None:
    monkeypatch.setattr(main, "_PIPELINE", Pipeline(llm, PipelineCache(), bundles={}))


def _loan_cobol() -> str:
    return (SAMPLES_DIR / "loan_calc.cbl").read_text(encoding="utf-8")


def test_custom_cobol_verifies_with_mocked_llm(client, monkeypatch):
    llm = StubLLMClient(
        rules=ParsedRules(["Payment is rounded."], []),
        generated=ArchitectOutput(reference_decimal_source("loan_calc"), {}),
    )
    _install(monkeypatch, llm)
    r = client.post(
        "/api/resurrect",
        json={"cobol_code": _loan_cobol(), "field_schema": LOAN_SCHEMA, "num_cases": 150},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["llm_used"] is True
    assert body["final_report"]["certified"] is True
    assert body["final_report"]["total_cases"] == 150


def test_preset_no_key_falls_through_to_503_for_custom(client, monkeypatch):
    class NoKey(StubLLMClient):
        def require_key(self):
            raise LLMKeyMissing("x")

    _install(monkeypatch, NoKey())
    r = client.post("/api/resurrect", json={"cobol_code": _loan_cobol(), "field_schema": LOAN_SCHEMA})
    assert r.status_code == 503
    assert "LLM_API_KEY" in r.json()["detail"]["message"]


@pytest.mark.parametrize(
    "exc,status", [(BudgetExceeded("b"), 503), (LLMCallError("cloud_architect", "t"), 502)]
)
def test_llm_error_mapping(client, monkeypatch, exc, status):
    _install(monkeypatch, StubLLMClient(raises=exc))
    r = client.post("/api/resurrect", json={"cobol_code": _loan_cobol(), "field_schema": LOAN_SCHEMA})
    assert r.status_code == status
    if status == 502:
        assert r.json()["detail"]["stage"] == "legacy_parser" or "cloud_architect" in str(r.json())


def test_rejected_cobol_makes_no_llm_call(client, monkeypatch):
    llm = StubLLMClient()
    _install(monkeypatch, llm)
    bad = _loan_cobol() + '\n       CALL "SYSTEM" USING "ls".\n'
    r = client.post("/api/resurrect", json={"cobol_code": bad, "field_schema": LOAN_SCHEMA})
    assert r.status_code == 400
    assert llm.call_count == 0


def test_request_validation(client, monkeypatch):
    llm = StubLLMClient()
    _install(monkeypatch, llm)
    code = _loan_cobol()
    cases = [
        {"cobol_code": code},  # no field_schema
        {"cobol_code": code, "field_schema": [{"name": "a", "pic": "9"}]},  # no direction
        {"preset_id": "loan_calc", "cobol_code": code, "field_schema": LOAN_SCHEMA},  # both
        {},  # neither
        {"preset_id": "loan_calc", "num_cases": 149},
        {"preset_id": "loan_calc", "num_cases": 201},
        {"cobol_code": code, "field_schema": [dict(LOAN_SCHEMA[0], min=1.5)]},  # float literal
        {"cobol_code": code, "field_schema": LOAN_SCHEMA, "demonstrate_self_healing": True},
    ]
    for payload in cases:
        assert client.post("/api/resurrect", json=payload).status_code == 422, payload
    assert llm.call_count == 0


def test_unknown_preset_lists_valid_ids(client):
    r = client.post("/api/resurrect", json={"preset_id": "nope"})
    assert r.status_code == 404
    assert r.json()["detail"]["valid_ids"] == ["loan_calc", "compound_int", "daily_ledger"]


def test_body_over_256_kib_is_413(client):
    r = client.post(
        "/api/resurrect",
        content=b" " * (256 * 1024 + 1),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413


def test_preset_demo_mode_self_heals(client, monkeypatch):
    llm = StubLLMClient()
    _install(monkeypatch, llm)
    r = client.post(
        "/api/resurrect", json={"preset_id": "loan_calc", "demonstrate_self_healing": True}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["audit_trail"]) == 2
    assert body["final_report"]["certified"] is True
    assert llm.call_count == 0


def test_export_served_from_bundle_only(client, monkeypatch):
    monkeypatch.setattr(main, "_PIPELINE", None)
    r = client.get("/api/export/loan_calc")
    if r.status_code == 404 and r.json()["detail"].get("error") == "bundle_missing":
        pytest.skip("preset_bundles.json not generated yet")
    assert r.status_code == 200
    body = r.json()
    assert body["certified"] is True and "def calculate" in body["verified_python_code"]
    assert main._PIPELINE is None  # no pipeline/LLM was constructed


def test_export_unknown_preset(client):
    r = client.get("/api/export/nope")
    assert r.status_code == 404
    assert r.json()["detail"]["valid_ids"] == ["loan_calc", "compound_int", "daily_ledger"]


def test_health_does_not_build_pipeline(client, monkeypatch):
    monkeypatch.setattr(main, "_PIPELINE", None)
    assert client.get("/health").status_code == 200
    assert main._PIPELINE is None
