"""Integration tests for the FastAPI endpoints (Task 9.8).

Covers /health, /api/presets, /api/verify and /api/calculate through
FastAPI's TestClient, against the real GnuCOBOL compiler.

Requirements: 14.1, 14.3, 14.4, 15.2, 15.3, 16.1, 16.4, 17.14, 17.15,
18.1, 18.2, 18.6.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import time
from decimal import Decimal
from pathlib import Path

os.environ["VERICOBOL_SKIP_WARMUP"] = "1"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.cobol_runner import CobolRunner  # noqa: E402

PRESET_IDS = ("loan_calc", "compound_int", "daily_ledger")
OUTPUT_FIELDS = {
    "loan_calc": ("monthly_payment", "total_interest", "total_payable"),
    "compound_int": ("maturity_amount", "accrued_interest"),
    "daily_ledger": ("closing_balance", "fee_applied", "overdraft_flag"),
}
LOAN_SCHEMA = [
    {"name": "principal", "min": "0.01", "max": "10000000.00", "step": "100.00"},
    {"name": "annual_rate_pct", "min": "0.000", "max": "100.000", "step": "0.001"},
    {"name": "term_months", "min": "1", "max": "360", "step": "1"},
]
MAIN_PATH = Path(main.__file__)


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture(autouse=True)
def _fresh_cobc_cache():
    main._cobc_info.cache_clear()
    yield
    main._cobc_info.cache_clear()


@pytest.fixture(scope="module")
def presets(client):
    return {p["preset_id"]: p for p in client.get("/api/presets").json()}


# ---------------------------------------------------------------- /health

def test_health_shape_and_single_cobc_call(client, monkeypatch):
    real_run = subprocess.run
    calls: list[list[str]] = []

    def spy(args, *a, **kw):
        calls.append(list(args))
        return real_run(args, *a, **kw)

    monkeypatch.setattr(main.subprocess, "run", spy)
    bodies = [client.get("/health") for _ in range(3)]
    for r in bodies:
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"status", "cobc_available", "cobc_version"}
        assert body["status"] == "ok"
        assert body["cobc_available"] is True
        assert isinstance(body["cobc_version"], str) and body["cobc_version"]
    assert len({r.text for r in bodies}) == 1
    version_calls = [c for c in calls if c[-1:] == ["--version"]]
    assert len(version_calls) == 1


def test_health_missing_cobc(client, monkeypatch):
    monkeypatch.setenv("COBC_PATH", "/nonexistent/definitely-not-cobc")
    main._cobc_info.cache_clear()
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "cobc_available": False, "cobc_version": ""}


# ------------------------------------------------------------------- CORS

def test_cors_allowed_and_disallowed_origin(client):
    allowed = main._frontend_origin()
    ok = client.get("/health", headers={"Origin": allowed})
    assert ok.headers.get("access-control-allow-origin") == allowed
    bad = client.get("/health", headers={"Origin": "https://evil.example.com"})
    assert bad.status_code == 200
    assert "access-control-allow-origin" not in bad.headers


def test_cors_preflight(client):
    allowed = main._frontend_origin()
    ok = client.options(
        "/api/verify",
        headers={"Origin": allowed, "Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": "Content-Type"},
    )
    assert ok.status_code == 200
    assert ok.headers.get("access-control-allow-origin") == allowed
    bad = client.options(
        "/api/verify",
        headers={"Origin": "https://evil.example.com",
                 "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in bad.headers


def _fresh_app(monkeypatch, frontend_url: str | None):
    if frontend_url is None:
        monkeypatch.delenv("FRONTEND_URL", raising=False)
    else:
        monkeypatch.setenv("FRONTEND_URL", frontend_url)
    spec = importlib.util.spec_from_file_location("app.main_cors_probe", MAIN_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.app


def test_cors_honours_frontend_url_env(monkeypatch):
    app = _fresh_app(monkeypatch, "https://bank.example.com/")
    with TestClient(app) as c:
        ok = c.get("/health", headers={"Origin": "https://bank.example.com"})
        assert ok.headers.get("access-control-allow-origin") == "https://bank.example.com"
        other = c.get("/health", headers={"Origin": "http://localhost:3000"})
        assert "access-control-allow-origin" not in other.headers


def test_cors_default_origin_when_unset(monkeypatch):
    app = _fresh_app(monkeypatch, None)
    with TestClient(app) as c:
        ok = c.get("/health", headers={"Origin": "http://localhost:3000"})
        assert ok.headers.get("access-control-allow-origin") == "http://localhost:3000"


# ----------------------------------------------------------- /api/presets

def test_presets_shape(presets):
    assert tuple(presets) == PRESET_IDS
    for pid, p in presets.items():
        assert {"preset_id", "name", "description", "cobol_source",
                "input_schema", "sample_input"} <= set(p)
        assert p["cobol_source"].strip()
        assert p["input_schema"]
        assert set(p["sample_input"]) == {f["name"] for f in p["input_schema"]}
        for v in p["sample_input"].values():
            assert isinstance(v, str)


def test_presets_read_once_cached(client, monkeypatch):
    client.get("/api/presets")  # ensure loaded

    def boom(*a, **kw):
        raise AssertionError("catalog file was re-read")

    monkeypatch.setattr(Path, "read_text", boom)
    monkeypatch.setattr(Path, "open", boom)
    r1 = client.get("/api/presets")
    r2 = client.get("/api/presets")
    assert r1.status_code == r2.status_code == 200
    assert r1.text == r2.text


# ------------------------------------------------------------ /api/verify

@pytest.mark.parametrize("preset_id", PRESET_IDS)
def test_verify_happy_path(client, preset_id):
    r = client.post("/api/verify", json={"preset_id": preset_id})
    assert r.status_code == 200
    body = r.json()
    assert body["total_cases"] == 150
    assert body["passed_cases"] == 150
    assert body["failed_cases"] == 0
    assert body["parity_score_pct"] == "100.00"
    assert body["verified"] is True
    assert body["counterexamples"] == []
    assert body["raw_cobol_sample_output"]


@pytest.mark.parametrize("preset_id", PRESET_IDS)
def test_verify_float_bug_flag_fails_cases(client, preset_id):
    r = client.post(
        "/api/verify", json={"preset_id": preset_id, "simulate_float_bug": True}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["failed_cases"] > 0
    assert body["verified"] is False
    assert body["counterexamples"]
    assert body["passed_cases"] + body["failed_cases"] == body["total_cases"]


def test_verify_repeated_calls_run_live_each_time(client, monkeypatch):
    real = CobolRunner.run_batch
    runs: list[int] = []

    def spy(self, binary_path, input_rows):
        runs.append(len(input_rows))
        return real(self, binary_path, input_rows)

    monkeypatch.setattr(CobolRunner, "run_batch", spy)
    payload = {"preset_id": "loan_calc"}
    r1 = client.post("/api/verify", json=payload)
    r2 = client.post("/api/verify", json=payload)
    assert r1.status_code == r2.status_code == 200
    assert len(runs) == 2
    assert all(n == 150 for n in runs)
    assert r1.json()["verified"] is True and r2.json()["verified"] is True


def test_verify_body_over_128kb_is_413(client):
    big = "A" * (128 * 1024 + 10)
    r = client.post("/api/verify", content=big,
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    r2 = client.post("/api/calculate", content=big,
                     headers={"Content-Type": "application/json"})
    assert r2.status_code == 413


def test_verify_validator_rejection_400_with_token_and_line(client):
    source = (
        "       IDENTIFICATION DIVISION.\n"
        "       PROGRAM-ID. EVIL.\n"
        "       PROCEDURE DIVISION.\n"
        '           CALL "SYSTEM" USING "echo hi".\n'
        "           STOP RUN.\n"
    )
    r = client.post("/api/verify", json={
        "cobol_code": source,
        "python_code": "def f(**kw):\n    return {}\n",
        "field_schema": LOAN_SCHEMA,
    })
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["error"] == "rejected"
    assert isinstance(detail["token"], str) and detail["token"]
    assert detail["line"] == 4
    assert detail["message"]


def test_verify_bad_requests(client):
    assert client.post("/api/verify", json={}).status_code == 422
    assert client.post("/api/verify", json={"preset_id": "nope"}).status_code == 404
    assert client.post("/api/verify", content="{not json").status_code == 422


# --------------------------------------------------------- /api/calculate

@pytest.mark.parametrize("preset_id", PRESET_IDS)
def test_calculate_happy_path(client, presets, preset_id):
    inputs = presets[preset_id]["sample_input"]
    r = client.post("/api/calculate", json={"preset_id": preset_id, "inputs": inputs})
    assert r.status_code == 200
    body = r.json()
    assert body["preset_id"] == preset_id
    assert set(body["result"]) == set(OUTPUT_FIELDS[preset_id])
    for name, value in body["result"].items():
        assert isinstance(value, str)
        Decimal(value)  # exact decimal text
    assert body["raw_cobol_output"].strip()


def test_calculate_known_values(client):
    r = client.post("/api/calculate", json={
        "preset_id": "daily_ledger",
        "inputs": {"opening_balance": "1000.00", "deposits": "250.50",
                   "withdrawals": "1500.75", "overdraft_fee": "35.00"},
    })
    assert r.status_code == 200
    res = r.json()["result"]
    assert Decimal(res["closing_balance"]) == Decimal("-250.25") - Decimal("35.00")
    assert Decimal(res["fee_applied"]) == Decimal("35.00")


@pytest.mark.parametrize("preset_id", PRESET_IDS)
def test_calculate_under_1_5_seconds(client, presets, preset_id):
    inputs = presets[preset_id]["sample_input"]
    payload = {"preset_id": preset_id, "inputs": inputs}
    assert client.post("/api/calculate", json=payload).status_code == 200  # warm
    start = time.perf_counter()
    r = client.post("/api/calculate", json=payload)
    elapsed = time.perf_counter() - start
    assert r.status_code == 200
    assert elapsed < 1.5


def test_calculate_rejects_bad_input(client):
    assert client.post("/api/calculate", json={"preset_id": "nope"}).status_code == 404
    r = client.post("/api/calculate", json={
        "preset_id": "loan_calc",
        "inputs": {"principal": "abc", "annual_rate_pct": "5", "term_months": "12"},
    })
    assert r.status_code == 422
    assert r.json()["detail"]["field"] == "principal"
