"""End-to-end tests of the Resurrection pipeline through the HTTP API (task 7.11).

Uses FastAPI's TestClient and the StubLLMClient from conftest.py, so no network
and no LLM tokens are involved. Verification still runs against real GnuCOBOL.

Cache design note: only *preset* runs are written to the pipeline cache. A
Custom_COBOL run is always live (it calls the LLM every time), so write-back is
tested with ``force_live_llm`` on a preset.
"""

from __future__ import annotations

import ast

import pytest
from fastapi.testclient import TestClient

from app import main
from app.llm_client import BudgetExceeded, LLMCallError, LLMClient, LLMKeyMissing
from app.pipeline_cache import PipelineCache, cache_key, normalize_cobol
from app.resurrection import (
    ArchitectOutput,
    ParsedRules,
    Pipeline,
    load_preset_bundles,
    reference_decimal_source,
)

from .conftest import PRESET_IDS, SAMPLES_DIR, StubLLMClient

NUM_CASES = 150
RESPONSE_KEYS = {
    "preset_id", "cache_hit", "llm_used", "stages", "business_rules", "pic_mappings",
    "dashboard_metadata", "verified_python_code", "export_fastapi_code",
    "audit_trail", "final_report",
}
STAGE_NAMES = ["legacy_parser", "cloud_architect", "parity_auditor"]

LOAN_SCHEMA = [
    {"name": "principal", "pic": "9(9)V99", "direction": "input"},
    {"name": "annual_rate_pct", "pic": "9(3)V999", "direction": "input"},
    {"name": "term_months", "pic": "9(3)", "direction": "input"},
    {"name": "monthly_payment", "pic": "9(9)V99", "direction": "output", "rounding": "ROUND_HALF_UP"},
    {"name": "total_interest", "pic": "9(9)V99", "direction": "output", "rounding": "ROUND_HALF_UP"},
    {"name": "total_payable", "pic": "9(9)V99", "direction": "output", "rounding": "ROUND_HALF_UP"},
]


def _cobol(preset_id: str = "loan_calc") -> str:
    return (SAMPLES_DIR / f"{preset_id}.cbl").read_text(encoding="utf-8")


def _truncating_loan_code() -> str:
    """Reference Decimal code with ROUNDED replaced by truncation (a classic bug)."""
    good = reference_decimal_source("loan_calc")
    bad = good.replace("_round_half_up(q, qs, 2)", "(q // 10 ** (qs - 2))")
    assert bad != good
    return bad


def _architect(code: str) -> ArchitectOutput:
    return ArchitectOutput(verified_python_code=code, dashboard_metadata={})


_RULES = ParsedRules(["Monthly payment is rounded half-up."], [])


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def install(monkeypatch):
    """Return ``install(llm, bundles=None) -> PipelineCache`` that swaps the pipeline."""

    def _install(llm, bundles=None):
        cache = PipelineCache()
        kwargs = {} if bundles is None else {"bundles": bundles}
        monkeypatch.setattr(main, "_PIPELINE", Pipeline(llm, cache, **kwargs))
        return cache

    return _install


def _resurrect(client, **payload):
    payload.setdefault("num_cases", NUM_CASES)
    return client.post("/api/resurrect", json=payload)


def _assert_no_float(code: str, where: str) -> None:
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "float":
            pytest.fail(f"{where}: references float")
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            pytest.fail(f"{where}: float literal {node.value!r}")
        if isinstance(node, ast.Attribute) and node.attr in ("pow", "sqrt", "exp", "log"):
            pytest.fail(f"{where}: math float call .{node.attr}")


# --------------------------------------------------------------------------
# Presets, normal mode
# --------------------------------------------------------------------------

@pytest.mark.parametrize("preset_id", PRESET_IDS)
def test_preset_cached_pipeline_certifies_on_attempt_one(client, install, preset_id):
    llm = StubLLMClient()
    install(llm)
    r = _resurrect(client, preset_id=preset_id, demonstrate_self_healing=False)
    assert r.status_code == 200, r.text
    body = r.json()

    assert set(body) == RESPONSE_KEYS and len(body) == 11
    assert body["preset_id"] == preset_id
    assert body["cache_hit"] is True
    assert body["llm_used"] is False
    assert [s["name"] for s in body["stages"]] == STAGE_NAMES
    assert body["stages"][2]["status"] == "ok"

    final = body["final_report"]
    assert final["certified"] is True and final["status"] == "passed"
    assert final["verified"] is True
    assert final["parity_score_pct"] == "100.00"
    assert final["total_cases"] == final["passed_cases"] == NUM_CASES
    assert final["failed_cases"] == 0 and final["counterexamples"] == []

    trail = body["audit_trail"]
    assert len(trail) == 1
    assert trail[0]["attempt"] == 1
    assert trail[0]["parity_score_pct"] == "100.00"
    assert trail[0]["counterexample"] is None
    assert llm.call_count == 0


# --------------------------------------------------------------------------
# Presets, self-healing demonstration
# --------------------------------------------------------------------------

@pytest.mark.parametrize("preset_id", PRESET_IDS)
def test_preset_self_healing_demo_fails_then_passes(client, install, preset_id):
    llm = StubLLMClient()
    install(llm)
    r = _resurrect(client, preset_id=preset_id, demonstrate_self_healing=True)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == RESPONSE_KEYS
    assert [s["name"] for s in body["stages"]] == STAGE_NAMES

    trail = body["audit_trail"]
    assert [a["attempt"] for a in trail] == [1, 2]

    first = trail[0]
    assert first["passed_cases"] < first["total_cases"] == NUM_CASES
    assert first["parity_score_pct"] != "100.00"
    cx = first["counterexample"]
    assert cx is not None
    assert set(cx) == {
        "inputs", "cobol_output", "python_output",
        "differing_field", "penny_delta", "likely_cause",
    }
    assert cx["likely_cause"] == "float_drift"
    assert cx["differing_field"] in cx["cobol_output"]
    assert cx["cobol_output"][cx["differing_field"]] != cx["python_output"][cx["differing_field"]]
    assert cx["inputs"]

    second = trail[1]
    assert second["parity_score_pct"] == "100.00"
    assert second["passed_cases"] == second["total_cases"] == NUM_CASES
    assert second["counterexample"] is None

    final = body["final_report"]
    assert final["certified"] is True and final["status"] == "passed"
    assert final["parity_score_pct"] == "100.00"
    assert final["counterexamples"] == []
    assert llm.call_count == 0


# --------------------------------------------------------------------------
# Custom COBOL with a mocked LLM
# --------------------------------------------------------------------------

def test_custom_cobol_repairs_truncation_bug_in_two_attempts(client, install):
    llm = StubLLMClient(
        rules=_RULES,
        generated=_architect(_truncating_loan_code()),
        repaired=_architect(reference_decimal_source("loan_calc")),
    )
    install(llm)
    r = _resurrect(client, cobol_code=_cobol(), field_schema=LOAN_SCHEMA)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["preset_id"] is None
    assert body["cache_hit"] is False and body["llm_used"] is True
    assert [s["name"] for s in body["stages"]] == STAGE_NAMES
    trail = body["audit_trail"]
    assert len(trail) == 2
    assert trail[0]["counterexample"] is not None
    assert trail[0]["counterexample"]["likely_cause"] in {"rounded_vs_truncation", "unknown", "scale_mismatch"}
    assert trail[0]["parity_score_pct"] != "100.00"
    assert trail[1]["parity_score_pct"] == "100.00"
    assert body["final_report"]["certified"] is True
    assert body["final_report"]["status"] == "passed"
    assert llm.calls == {"extract_rules": 1, "generate_python": 1, "repair_python": 1}
    # The repair call received the counterexample and the buggy previous code.
    _, args = llm.call_log[-1]
    assert args[3].differing_field and args[4] == _truncating_loan_code()


def test_custom_cobol_always_buggy_llm_fails_after_three_attempts(client, install):
    bad = _architect(_truncating_loan_code())
    llm = StubLLMClient(rules=_RULES, generated=bad, repaired=bad)
    install(llm)
    r = _resurrect(client, cobol_code=_cobol(), field_schema=LOAN_SCHEMA)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["preset_id"] is None
    assert len(body["audit_trail"]) == 3
    assert [a["attempt"] for a in body["audit_trail"]] == [1, 2, 3]
    assert all(a["counterexample"] is not None for a in body["audit_trail"])
    final = body["final_report"]
    assert final["certified"] is False and final["status"] == "failed"
    assert final["verified"] is False
    assert final["parity_score_pct"] != "100.00"
    assert final["counterexamples"]
    assert body["stages"][2]["name"] == "parity_auditor"
    assert body["stages"][2]["status"] == "failed"
    assert llm.calls["repair_python"] == 2  # max 2 self-healing retries


# --------------------------------------------------------------------------
# SHA-256 cache
# --------------------------------------------------------------------------

def test_cache_key_normalization_and_demo_flag():
    src = _cobol("loan_calc")
    key = cache_key(src, False)
    assert len(key) == 64 and int(key, 16) >= 0
    noisy = "\n\n" + src.replace("\n", "   \r\n") + "\n\n\n"
    assert normalize_cobol(noisy) == normalize_cobol(src)
    assert cache_key(noisy, False) == key
    assert cache_key(src, True) != key  # demo flag separates entries
    assert cache_key(src + "\n       DISPLAY 'X'.", False) != key
    assert cache_key(_cobol("compound_int"), False) != key


def test_force_live_llm_preset_writes_back_then_repeat_is_cached(client, install):
    llm = StubLLMClient(
        rules=_RULES, generated=_architect(reference_decimal_source("loan_calc"))
    )
    cache = install(llm, bundles={})  # empty bundles: nothing is pre-seeded
    key = cache_key(_cobol("loan_calc"), False)
    assert key not in cache

    first = _resurrect(client, preset_id="loan_calc", force_live_llm=True)
    assert first.status_code == 200, first.text
    b1 = first.json()
    assert b1["cache_hit"] is False and b1["llm_used"] is True
    assert b1["final_report"]["certified"] is True
    assert llm.call_count == 2  # extract_rules + generate_python
    assert key in cache

    second = _resurrect(client, preset_id="loan_calc")
    assert second.status_code == 200, second.text
    b2 = second.json()
    assert b2["cache_hit"] is True and b2["llm_used"] is False
    assert llm.call_count == 2  # no additional LLM calls
    assert b2["verified_python_code"] == b1["verified_python_code"]
    # A cache hit still runs live GnuCOBOL verification.
    assert b2["final_report"]["certified"] is True
    assert b2["final_report"]["total_cases"] == NUM_CASES


def test_custom_cobol_is_not_cached_and_calls_llm_every_time(client, install):
    llm = StubLLMClient(
        rules=_RULES, generated=_architect(reference_decimal_source("loan_calc"))
    )
    cache = install(llm, bundles={})
    for expected_calls in (2, 4):
        r = _resurrect(client, cobol_code=_cobol(), field_schema=LOAN_SCHEMA)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["preset_id"] is None and body["cache_hit"] is False
        assert body["final_report"]["certified"] is True
        assert llm.call_count == expected_calls
    assert len(cache) == 0


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(lambda: {"cobol_code": _cobol()}, id="missing-field_schema"),
        pytest.param(
            lambda: {"cobol_code": _cobol(), "field_schema": []}, id="empty-field_schema"
        ),
        pytest.param(
            lambda: {"preset_id": "loan_calc", "cobol_code": _cobol(), "field_schema": LOAN_SCHEMA},
            id="both",
        ),
        pytest.param(lambda: {}, id="neither"),
    ],
)
def test_invalid_request_shapes_are_4xx_and_make_no_llm_call(client, install, payload):
    llm = StubLLMClient()
    install(llm)
    r = client.post("/api/resurrect", json=payload())
    assert 400 <= r.status_code < 500
    assert r.status_code == 422
    assert llm.call_count == 0


def test_oversized_body_is_413(client, install):
    llm = StubLLMClient()
    install(llm)
    r = client.post(
        "/api/resurrect",
        content=b" " * (256 * 1024 + 1),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413
    assert llm.call_count == 0


def test_custom_without_api_key_is_503_mentioning_llm_api_key(client, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setattr(main, "_PIPELINE", None)
    r = _resurrect(client, cobol_code=_cobol(), field_schema=LOAN_SCHEMA)
    assert r.status_code == 503
    assert "LLM_API_KEY" in r.json()["detail"]["message"]
    assert r.json()["detail"]["error"] == "llm_key_missing"


def test_custom_with_key_missing_stub_is_503(client, install):
    class NoKey(StubLLMClient):
        def require_key(self):
            raise LLMKeyMissing("LLM_API_KEY is not set")

    llm = NoKey()
    install(llm)
    r = _resurrect(client, cobol_code=_cobol(), field_schema=LOAN_SCHEMA)
    assert r.status_code == 503
    assert "LLM_API_KEY" in r.json()["detail"]["message"]
    assert llm.calls["extract_rules"] == 0


def test_preset_works_without_api_key(client, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setattr(main, "_PIPELINE", Pipeline(LLMClient.from_env(), PipelineCache()))
    r = _resurrect(client, preset_id="loan_calc")
    assert r.status_code == 200, r.text
    assert r.json()["final_report"]["certified"] is True


def test_llm_failure_is_502(client, install):
    llm = StubLLMClient(raises=LLMCallError("legacy_parser", "boom"))
    install(llm)
    r = _resurrect(client, cobol_code=_cobol(), field_schema=LOAN_SCHEMA)
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert detail["error"] == "llm_call_failed"
    assert detail["stage"] == "legacy_parser"


def test_budget_exhausted_is_503(client, install):
    llm = StubLLMClient(raises=BudgetExceeded("budget"))
    install(llm)
    r = _resurrect(client, cobol_code=_cobol(), field_schema=LOAN_SCHEMA)
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "budget_exhausted"


def test_unknown_preset_is_404_with_valid_ids(client, install):
    install(StubLLMClient())
    r = _resurrect(client, preset_id="nope")
    assert r.status_code == 404
    assert r.json()["detail"]["valid_ids"] == list(PRESET_IDS)


# --------------------------------------------------------------------------
# Export endpoint, health, and independence from LLM/compiler
# --------------------------------------------------------------------------

@pytest.mark.parametrize("preset_id", PRESET_IDS)
def test_export_happy_path(client, preset_id):
    r = client.get(f"/api/export/{preset_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["preset_id"] == preset_id
    assert body["certified"] is True
    assert "def calculate" in body["verified_python_code"]
    assert body["export_fastapi_code"].strip()
    assert body["parity_certificate"]["parity_score_pct"] == "100.00"
    ast.parse(body["verified_python_code"])
    ast.parse(body["export_fastapi_code"])


def test_export_unknown_preset_is_404(client):
    r = client.get("/api/export/not_a_preset")
    assert r.status_code == 404
    assert r.json()["detail"]["valid_ids"] == list(PRESET_IDS)


def test_health_and_export_work_with_llm_and_compiler_broken(client, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("must not be called")

    monkeypatch.setattr(main, "_PIPELINE", None)
    monkeypatch.setattr(LLMClient, "from_env", classmethod(lambda cls, *a, **k: boom()))
    monkeypatch.setattr(main, "_get_pipeline", boom)

    def no_compiler(*_a, **_k):
        raise FileNotFoundError("cobc not installed")

    # /health probes ``cobc --version`` and must degrade gracefully, not fail.
    monkeypatch.setattr(main.subprocess, "run", no_compiler)
    import app.pbt_verifier as pbt

    monkeypatch.setattr(pbt, "_shared_runner", boom)
    main._cobc_info.cache_clear()
    try:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert health.json()["cobc_available"] is False
        export = client.get("/api/export/loan_calc")
        assert export.status_code == 200
        assert export.json()["certified"] is True
    finally:
        main._cobc_info.cache_clear()
    assert main._PIPELINE is None


# --------------------------------------------------------------------------
# No float in bundled code
# --------------------------------------------------------------------------

@pytest.mark.parametrize("preset_id", PRESET_IDS)
def test_bundled_results_contain_no_float(client, preset_id):
    bundles = load_preset_bundles()
    assert preset_id in bundles
    for flag, entry in bundles[preset_id].items():
        for key in ("verified_python_code", "export_fastapi_code"):
            _assert_no_float(entry[key], f"bundle {preset_id}/{flag}/{key}")
    exported = client.get(f"/api/export/{preset_id}").json()
    for key in ("verified_python_code", "export_fastapi_code"):
        _assert_no_float(exported[key], f"export {preset_id}/{key}")
