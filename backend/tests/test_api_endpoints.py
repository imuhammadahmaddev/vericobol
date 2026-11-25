"""Integration tests for /health, CORS, /api/presets and removed endpoints.

Runs through FastAPI's TestClient. The resurrect pipeline itself is covered by
test_resurrect_api.py and test_resurrection_pipeline.py.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

os.environ["VERICOBOL_SKIP_WARMUP"] = "1"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402

PRESET_IDS = ("loan_calc", "compound_int", "daily_ledger")
SAMPLES_DIR = Path(__file__).resolve().parent.parent / "cobol_samples"
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
    return {p["id"]: p for p in client.get("/api/presets").json()}


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
        assert set(body) == {"status", "cobc_available", "cobc_version", "llm_configured"}
        assert isinstance(body["llm_configured"], bool)
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
    body = r.json()
    assert body["cobc_available"] is False and body["cobc_version"] == ""
    assert body["status"] == "ok"


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
        "/api/resurrect",
        headers={"Origin": allowed, "Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": "Content-Type"},
    )
    assert ok.status_code == 200
    assert ok.headers.get("access-control-allow-origin") == allowed
    bad = client.options(
        "/api/resurrect",
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

def test_presets_return_id_title_and_raw_cobol_only(presets):
    assert tuple(presets) == PRESET_IDS
    for pid, p in presets.items():
        assert set(p) == {"id", "title", "cobol_code"}
        assert p["title"].strip()
        assert p["cobol_code"] == (SAMPLES_DIR / f"{pid}.cbl").read_text(encoding="utf-8")


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


# ------------------------------------------------------- removed endpoints

@pytest.mark.parametrize("path", ["/api/verify", "/api/calculate", "/api/export/loan_calc"])
def test_preset_only_endpoints_are_gone(client, path):
    assert client.post(path, json={"preset_id": "loan_calc"}).status_code in (404, 405)
    assert client.get(path).status_code in (404, 405)


# ------------------------------------------------------------ body limits

def test_body_over_128kb_is_413_on_small_endpoints(client):
    big = "A" * (128 * 1024 + 10)
    r = client.post("/api/presets", content=big, headers={"Content-Type": "application/json"})
    assert r.status_code == 413
