"""Unit tests for the resurrection data models and contract-derived metadata.

**Validates: Requirements 1.2, 1.5, 1.7, 2.3, 2.4, 7.2, 7.4, 7.8**
"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from app.catalog import DecimalJSONResponse, load_presets
from app.pbt_verifier import Counterexample, ParityReport
from app.resurrection import (
    AttemptRecord,
    FinalReport,
    PipelineStageError,
    ResurrectResponse,
    StageRecord,
    build_dashboard_metadata,
    build_pic_mappings,
    contract_from_field_schema,
    contract_from_preset,
    parse_pic,
)

PRESETS = ("loan_calc", "compound_int", "daily_ledger")


@pytest.mark.parametrize("pic,expected", [
    ("9(9)V99", (False, 9, 2)),
    ("S9(9)V99", (True, 9, 2)),
    ("9(3)V999", (False, 3, 3)),
    ("99", (False, 2, 0)),
    ("9(1)", (False, 1, 0)),
])
def test_parse_pic(pic, expected):
    assert parse_pic(pic) == expected


def test_parse_pic_rejects_text():
    with pytest.raises(ValueError):
        parse_pic("X(10)")


@pytest.mark.parametrize("preset_id", PRESETS)
def test_preset_contract_matches_catalog(preset_id):
    entry = next(p for p in load_presets() if p["preset_id"] == preset_id)
    schema = contract_from_preset(preset_id)
    assert [f.name for f in schema.input_fields] == [f["name"] for f in entry["input_schema"]]
    for f, cat in zip(schema.input_fields, entry["input_schema"]):
        assert (f.min, f.max, f.step) == (Decimal(cat["min"]), Decimal(cat["max"]), Decimal(cat["step"]))
    assert schema.output_fields


def test_unknown_preset_lists_valid_ids():
    with pytest.raises(ValueError, match="loan_calc"):
        contract_from_preset("nope")


def test_custom_schema_defaults_bounds_from_pic():
    schema = contract_from_field_schema([
        {"name": "amt", "pic": "S9(3)V99", "direction": "input"},
        {"name": "out", "pic": "9(4)V99", "direction": "output", "rounding": "ROUND_HALF_UP"},
    ])
    amt = schema.input_fields[0]
    assert (amt.scale, amt.min, amt.max, amt.step) == (2, Decimal("-999.99"), Decimal("999.99"), Decimal("0.01"))
    assert schema.output_fields[0].rounding == "ROUND_HALF_UP"


@pytest.mark.parametrize("bad", [
    [],
    [{"name": "a", "pic": "9(3)", "direction": "input"}],
    [{"name": "", "pic": "9(3)", "direction": "input"}, {"name": "o", "pic": "9", "direction": "output"}],
    [{"name": "a", "pic": "", "direction": "input"}, {"name": "o", "pic": "9", "direction": "output"}],
    [{"name": "a", "pic": "9", "direction": "sideways"}, {"name": "o", "pic": "9", "direction": "output"}],
])
def test_custom_schema_rejects_incomplete(bad):
    with pytest.raises(ValueError):
        contract_from_field_schema(bad)


def test_custom_schema_rejects_binary_float_bound():
    with pytest.raises(TypeError):
        contract_from_field_schema([
            {"name": "a", "pic": "9(3)V99", "direction": "input", "min": 0.1 * 3},
            {"name": "o", "pic": "9", "direction": "output"},
        ])


@pytest.mark.parametrize("preset_id", PRESETS)
def test_metadata_ignores_adversarial_llm_values(preset_id):
    schema = contract_from_preset(preset_id)
    first = schema.input_fields[0]
    evil = {
        "inputs": [
            {"name": first.name, "label": "Nice Label", "min": "-1", "max": "9" * 30, "step": "7"},
            {"name": "invented", "min": "0", "max": "1", "step": "1"},
        ],
        "outputs": [{"name": "ghost", "format": "currency"}],
    }
    meta = build_dashboard_metadata(schema, evil)
    assert [c["name"] for c in meta["inputs"]] == [f.name for f in schema.input_fields]
    assert [c["name"] for c in meta["outputs"]] == [f.name for f in schema.output_fields]
    ctl = meta["inputs"][0]
    assert ctl["label"] == "Nice Label"
    assert (ctl["min"], ctl["max"], ctl["step"]) == (first.min, first.max, first.step)

    mappings = build_pic_mappings(schema, [{"name": "invented", "pic": "X", "scale": "9", "rounding": "x"}])
    assert [m["name"] for m in mappings] == [f.name for f in schema.all_fields]
    assert all(set(m) == {"name", "pic", "scale", "rounding"} for m in mappings)


def _response() -> ResurrectResponse:
    cex = Counterexample({"a": "1"}, {"o": "1.00"}, {"o": "1.01"}, "o", "0.01", "float drift")
    report = ParityReport.build(0, 1, 1.0, [], [cex])
    return ResurrectResponse(
        preset_id=None,
        cache_hit=False,
        llm_used=True,
        stages=[StageRecord("legacy_parser", "ok", 3, "llm")],
        business_rules=["r"],
        pic_mappings=[],
        dashboard_metadata=build_dashboard_metadata(contract_from_preset("loan_calc")),
        verified_python_code="x",
        export_fastapi_code="y",
        audit_trail=[AttemptRecord(1, "0.00", 0, 1, cex)],
        final_report=FinalReport(report, False, "failed"),
    )


def test_response_to_dict_has_eleven_keys_and_keeps_decimals():
    body = _response().to_dict()
    assert len(body) == 11 and body["preset_id"] is None
    assert body["dashboard_metadata"]["inputs"][0]["min"] == Decimal("0.01")
    assert body["final_report"]["certified"] is False
    rendered = json.loads(DecimalJSONResponse(body).body)
    assert rendered["dashboard_metadata"]["inputs"][0]["min"] == "0.01"
    assert rendered["audit_trail"][0]["counterexample"]["differing_field"] == "o"


def test_models_are_frozen_and_stage_error_carries_fields():
    with pytest.raises(FrozenInstanceError):
        _response().cache_hit = True  # type: ignore[misc]
    err = PipelineStageError(504, "parity_auditor", {"error": "run_timeout"})
    assert (err.status, err.stage, err.body) == (504, "parity_auditor", {"error": "run_timeout"})
