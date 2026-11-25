"""Example tests for Pipeline orchestration (resurrection-pipeline task 5.1).

Real GnuCOBOL is used for every verification; only the LLM is a stub.
"""

from __future__ import annotations

from app import pbt_verifier, resurrection
from app.export_template import render
from app.resurrection import (
    ArchitectOutput,
    ParsedRules,
    Pipeline,
    contract_from_preset,
    reference_decimal_source,
)

_BAD_CODE = (
    "def calculate(**kwargs):\n"
    "    return {'monthly_payment': Decimal('0.00'), 'total_interest': Decimal('0.00'),"
    " 'total_payable': Decimal('0.00')}\n"
)


def test_demo_mode_preset_fails_then_repairs(stub_llm, pipeline_cache):
    schema = contract_from_preset("loan_calc")
    result = Pipeline(stub_llm, pipeline_cache, bundles={}).run(
        preset_id="loan_calc", cobol_source=None, schema=schema, num_cases=150,
        demonstrate_self_healing=True, force_live_llm=False,
    )
    trail = result.audit_trail
    assert [a.attempt for a in trail] == [1, 2]
    assert trail[0].counterexample is not None
    assert trail[0].counterexample.likely_cause == "float_drift"
    assert trail[1].counterexample is None
    assert result.final_report.certified
    assert result.final_report.report.parity_score_pct == "100.00"
    assert result.llm_used is False and result.cache_hit is False
    assert stub_llm.call_count == 0
    assert "float" not in result.verified_python_code
    assert len(pipeline_cache) == 1  # certified live run is cached


def test_custom_cobol_heals_with_llm_repair(stub_llm_factory, pipeline_cache, monkeypatch):
    real = pbt_verifier.run_verification
    # The engine takes its input layout from a preset id; custom source uses loan_calc's.
    monkeypatch.setattr(
        resurrection, "run_verification",
        lambda pid, src, **kw: real("loan_calc", src, **kw),
    )
    schema = contract_from_preset("loan_calc")
    good = ArchitectOutput(reference_decimal_source("loan_calc"), {})
    llm = stub_llm_factory(
        rules=ParsedRules(["rule"], []),
        generated=ArchitectOutput(_BAD_CODE, {}),
        repaired=good,
    )
    source = resurrection._preset_source("loan_calc")
    result = Pipeline(llm, pipeline_cache, bundles={}).run(
        preset_id=None, cobol_source=source, schema=schema, num_cases=150,
        demonstrate_self_healing=False, force_live_llm=False,
    )
    assert result.preset_id is None and result.llm_used is True
    assert [a.counterexample is None for a in result.audit_trail] == [False, True]
    assert result.final_report.certified
    assert result.verified_python_code == good.verified_python_code
    assert llm.calls == {"extract_rules": 1, "generate_python": 1, "repair_python": 1}
    first_cx = llm.call_log[-1][1][3]
    assert first_cx == result.audit_trail[0].counterexample
    assert len(pipeline_cache) == 0  # custom runs are not cached


def test_bundle_hit_makes_no_llm_calls_and_reverifies(stub_llm, pipeline_cache):
    schema = contract_from_preset("loan_calc")
    code = reference_decimal_source("loan_calc")
    entry = {
        "business_rules": ["Payment is rounded half-up."],
        "pic_mappings": [],
        "dashboard_metadata": {"inputs": [], "outputs": []},
        "verified_python_code": code,
        "export_fastapi_code": render(code, schema),
        "stages": [],
    }
    pipeline = Pipeline(stub_llm, pipeline_cache, bundles={"loan_calc": {"false": entry}})
    result = pipeline.run(
        preset_id="loan_calc", cobol_source=None, schema=schema, num_cases=150,
        demonstrate_self_healing=False, force_live_llm=False,
    )
    assert result.cache_hit is True and result.llm_used is False
    assert stub_llm.call_count == 0 and not getattr(stub_llm, "key_checks", 0)
    assert result.final_report.certified and len(result.audit_trail) == 1
    assert result.final_report.report.total_cases == 150
    assert [s.source for s in result.stages[:2]] == ["cache", "cache"]

    # A stale entry fails verification instead of being repaired.
    stale = dict(entry, verified_python_code=_BAD_CODE)
    stale_cache = type(pipeline_cache)()
    stale_result = Pipeline(stub_llm, stale_cache, bundles={"loan_calc": {"false": stale}}).run(
        preset_id="loan_calc", cobol_source=None, schema=schema, num_cases=150,
        demonstrate_self_healing=False, force_live_llm=False,
    )
    assert stale_result.final_report.certified is False
    assert len(stale_result.audit_trail) == 1
    assert stub_llm.call_count == 0
