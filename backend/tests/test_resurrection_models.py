"""Unit tests for the resurrection data models and contract-derived metadata."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from app.catalog import DecimalJSONResponse
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
    normalize_contract,
    parse_pic,
    schema_from_contract,
)

from .conftest import preset_program

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


def test_normalize_contract_fills_missing_bounds_and_rounding():
    contract = {
        "inputs": [{"name": "amt", "label": "Amt", "pic": "S9(3)V99", "scale": 2}],
        "outputs": [{"name": "o", "label": "O", "pic": "9(4)V99", "scale": 2,
                     "rounding": "rounded", "format": "currency"}],
    }
    norm = normalize_contract(contract)
    amt = norm["inputs"][0]
    assert (amt["min"], amt["max"], amt["step"]) == ("-999.99", "999.99", "0.01")
    assert norm["outputs"][0]["rounding"] == "ROUND_HALF_UP"
    assert "min" not in contract["inputs"][0]  # the input is not mutated
    typed = schema_from_contract(norm)
    assert typed.input_fields[0].min == Decimal("-999.99")
    assert typed.output_fields[0].rounding == "ROUND_HALF_UP"


@pytest.mark.parametrize("preset_id", PRESETS)
def test_metadata_comes_from_the_contract_not_the_llm(preset_id):
    program = preset_program(preset_id)
    first = program.contract["inputs"][0]
    evil = dict(program.dashboard_metadata, title="  Nice Title  ", chart_type="not_a_chart")
    evil["inputs"] = [{"name": "invented", "min": "0", "max": "1", "step": "1"}]
    evil["outputs"] = [{"name": "ghost", "format": "currency"}]
    program = type(program)(**{**program.__dict__, "dashboard_metadata": evil})

    meta = build_dashboard_metadata(program)
    assert meta["title"] == "Nice Title" and meta["chart_type"] == "summary"
    assert [c["name"] for c in meta["inputs"]] == [f["name"] for f in program.contract["inputs"]]
    assert [c["name"] for c in meta["outputs"]] == [f["name"] for f in program.contract["outputs"]]
    ctl = meta["inputs"][0]
    assert (ctl["min"], ctl["max"], ctl["step"]) == (first["min"], first["max"], first["step"])

    mappings = build_pic_mappings(schema_from_contract(normalize_contract(program.contract)))
    assert [m["name"] for m in mappings] == [
        f["name"] for f in program.contract["inputs"] + program.contract["outputs"]
    ]
    assert all(set(m) == {"name", "pic", "scale", "rounding"} for m in mappings)


def _response() -> ResurrectResponse:
    program = preset_program("loan_calc")
    cex = Counterexample({"a": "1"}, {"o": "1.00"}, {"o": "1.01"}, "o", "0.01", "float_drift")
    report = ParityReport.build(0, 1, 1, [], [cex])
    return ResurrectResponse(
        cobol_code="SRC",
        program_title=program.program_title,
        description=program.description,
        io_protocol=program.io_protocol,
        contract=program.contract,
        cache_hit=False,
        llm_used=True,
        stages=[StageRecord("legacy_parser", "ok", 3, "llm")],
        business_rules=["r"],
        pic_mappings=[],
        dashboard_metadata=build_dashboard_metadata(program),
        verified_python_code="x",
        export_fastapi_code="y",
        audit_trail=[AttemptRecord(1, "0.00", 0, 1, cex)],
        final_report=FinalReport(report, False, "failed"),
    )


def test_response_to_dict_keeps_decimals_as_strings_in_json():
    body = _response().to_dict()
    assert body["preset_id"] is None and body["certified"] is False
    assert body["final_report"]["certified"] is False
    rendered = json.loads(DecimalJSONResponse(body).body)
    assert rendered["dashboard_metadata"]["inputs"][0]["min"] == "0.01"
    assert rendered["audit_trail"][0]["counterexample"]["differing_field"] == "o"


def test_models_are_frozen_and_stage_error_carries_fields():
    with pytest.raises(FrozenInstanceError):
        _response().cache_hit = True  # type: ignore[misc]
    err = PipelineStageError(504, "parity_auditor", {"error": "run_timeout"})
    assert (err.status, err.stage, err.body) == (504, "parity_auditor", {"error": "run_timeout"})
