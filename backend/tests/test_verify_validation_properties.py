"""POST /api/resurrect request validation is a pure function of the request shape.

Every shape below is rejected before any LLM call or COBOL run, so the tests are
cheap and need no ``cobc``. The pipeline is replaced by a scripted LLM whose call
counter must stay at 0.
"""

# Feature: universal-open-cobol-platform, request validation matrix for /api/resurrect

from __future__ import annotations

import json
import os

os.environ["VERICOBOL_SKIP_WARMUP"] = "1"

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings, strategies as st

from app import main
from app.pipeline_cache import PipelineCache
from app.resurrection import Pipeline

from .conftest import PRESET_IDS, ScriptedLLM

PBT = settings(
    max_examples=100,
    database=None,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)

_FLOAT_MARK = "__FLOAT_LITERAL__"

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def llm(monkeypatch):
    stub = ScriptedLLM(generated="")
    monkeypatch.setattr(main, "_PIPELINE", Pipeline(stub, PipelineCache()))
    yield stub
    assert stub.call_count == 0  # a rejected request never reaches the LLM


def _post(body: dict) -> int:
    text = json.dumps(body).replace(f'"{_FLOAT_MARK}"', "1.5")
    resp = client.post(
        "/api/resurrect", content=text, headers={"Content-Type": "application/json"}
    )
    return resp.status_code


layouts = st.sampled_from(PRESET_IDS)
safe_text = st.text(
    alphabet=st.characters(min_codepoint=65, max_codepoint=90), min_size=0, max_size=30
)
safe_cobol = safe_text.map(lambda s: f"       STOP RUN. {s}")
prohibited_cobol = st.sampled_from(
    [
        '       CALL "SYSTEM" USING "ls".',
        "       OPEN OUTPUT OUT-FILE.",
        "       DISPLAY X UPON ENVIRONMENT-NAME.",
        "       ACCEPT X FROM ENVIRONMENT Y.",
    ]
).flatmap(lambda bad: safe_text.map(lambda s: f"       STOP RUN. {s}\n{bad}\n"))

bad_num_cases = st.one_of(
    st.integers(max_value=149),
    st.integers(min_value=201),
    st.just(_FLOAT_MARK),  # 1.5, a JSON float literal
    st.text(min_size=0, max_size=5),
    st.booleans(),
    st.lists(st.integers(), max_size=2),
)


@PBT
@given(cobol=safe_cobol, preset=st.one_of(layouts, st.just("zzz_unknown")))
def test_both_preset_and_cobol_is_422(cobol, preset):
    assert _post({"preset_id": preset, "cobol_code": cobol}) == 422


@PBT
@given(flag=st.booleans(), force=st.booleans())
def test_neither_preset_nor_cobol_is_422(flag, force):
    assert _post({"demonstrate_self_healing": flag, "force_live_llm": force}) == 422


@PBT
@given(name=st.from_regex(r"[a-z][a-z0-9_]{0,20}", fullmatch=True))
def test_unknown_preset_is_404(name):
    if name in PRESET_IDS:
        return
    assert _post({"preset_id": name}) == 404


@PBT
@given(preset=st.one_of(layouts, st.just("zzz_unknown")), nc=bad_num_cases)
def test_num_cases_out_of_range_or_non_integer_is_422(preset, nc):
    # Shape validation precedes the 404 lookup, so an unknown preset still yields 422.
    assert _post({"preset_id": preset, "num_cases": nc}) == 422


@PBT
@given(cobol=safe_cobol, extra=st.sampled_from(["python_code", "field_schema", "simulate_float_bug"]))
def test_removed_request_fields_are_422(cobol, extra):
    assert _post({"cobol_code": cobol, extra: "x"}) == 422


@PBT
@given(cobol=prohibited_cobol)
def test_prohibited_construct_is_400(cobol):
    assert _post({"cobol_code": cobol}) == 400


@PBT
@given(
    shape=st.sampled_from(["neither", "both", "unknown", "bad_nc", "prohibited"]),
    data=st.data(),
)
def test_status_depends_only_on_shape(shape, data):
    expected = {"neither": 422, "both": 422, "unknown": 404, "bad_nc": 422, "prohibited": 400}[shape]
    statuses = set()
    for _ in range(2):
        if shape == "neither":
            body = {}
        elif shape == "both":
            body = {"preset_id": data.draw(layouts), "cobol_code": data.draw(safe_cobol)}
        elif shape == "unknown":
            body = {"preset_id": "q" + data.draw(st.from_regex(r"[a-z]{3,8}", fullmatch=True))}
        elif shape == "bad_nc":
            body = {"preset_id": data.draw(layouts), "num_cases": data.draw(bad_num_cases)}
        else:
            body = {"cobol_code": data.draw(prohibited_cobol)}
        statuses.add(_post(body))
    assert statuses == {expected}
