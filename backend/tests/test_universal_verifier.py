"""Protocol-adapter tests for the universal Verification_Engine (task 4.1).

These run real GnuCOBOL and a forked Python child, so they are skipped on hosts
without ``cobc`` or ``fork`` (for example a Windows developer machine). Run them
in the Docker image.
"""

from __future__ import annotations

import json
import multiprocessing
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from app import pbt_verifier
from app.pbt_verifier import (
    generate_universal_cases,
    parse_outputs,
    run_universal_verification,
)

SAMPLES = Path(__file__).resolve().parent.parent / "cobol_samples"

needs_runtime = pytest.mark.skipif(
    shutil.which("cobc") is None or "fork" not in multiprocessing.get_all_start_methods(),
    reason="needs cobc and fork (use the Docker image)",
)

TAX_COBOL = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TAXCALC.
DATA DIVISION.
WORKING-STORAGE SECTION.
01 WS-GROSS-X PIC X(30).
01 WS-RATE-X PIC X(30).
01 WS-GROSS PIC S9(7)V99.
01 WS-RATE PIC 9(2)V99.
01 WS-TAX PIC S9(7)V99.
01 WS-OUT PIC -(7)9.99.
PROCEDURE DIVISION.
    ACCEPT WS-GROSS-X
    ACCEPT WS-RATE-X
    MOVE FUNCTION NUMVAL(WS-GROSS-X) TO WS-GROSS
    MOVE FUNCTION NUMVAL(WS-RATE-X) TO WS-RATE
    COMPUTE WS-TAX = WS-GROSS * WS-RATE / 100
    MOVE WS-TAX TO WS-OUT
    DISPLAY "TAX: " WS-OUT
    STOP RUN.
"""

TAX_CONTRACT = {
    "inputs": [
        {"name": "gross", "pic": "S9(7)V99", "scale": 2, "min": "-1000000.00", "max": "9999999.99"},
        {"name": "rate", "pic": "9(2)V99", "scale": 2, "min": "0.00", "max": "99.99"},
    ],
    "outputs": [{"name": "tax", "pic": "S9(7)V99", "scale": 2}],
}

TAX_PY = (
    "def calculate(gross, rate):\n"
    "    tax = (gross * rate / Decimal(100)).quantize(Decimal('0.01'), rounding=ROUND_DOWN)\n"
    "    return {'tax': tax}\n"
)
TAX_FLOAT_PY = (
    "def calculate(gross, rate):\n"
    "    return {'tax': float(gross) * float(rate) / 100}\n"
)

STATIC_COBOL = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. STATICP.
DATA DIVISION.
WORKING-STORAGE SECTION.
01 WS-OUT PIC 9(3).99.
PROCEDURE DIVISION.
    MOVE 12.5 TO WS-OUT
    DISPLAY "RESULT: " WS-OUT
    STOP RUN.
"""
STATIC_CONTRACT = {"inputs": [], "outputs": [{"name": "result", "pic": "9(3)V99", "scale": 2}]}


# -- pure unit tests (no cobc needed) ---------------------------------------

def test_import_does_not_require_resource_module():
    assert hasattr(pbt_verifier, "resource")  # None on Windows, the module on Linux


def test_parse_outputs_labeled_and_edited_forms():
    assert parse_outputs("TAX:      -12.30\n", 1, "single_run_lines") == ["-12.30"]
    assert parse_outputs("Net: 1,234.50\nTax: 5.00-\n", 2, "single_run_lines") == ["1234.50", "-5.00"]
    assert parse_outputs("1.00 2.00 3.00", 3, "batch_space") == ["1.00", "2.00", "3.00"]
    assert parse_outputs("1.00 2.00", 3, "batch_space") is None


@pytest.mark.parametrize("protocol,expected", [
    ("batch_space", 150), ("single_run_lines", 40), ("single_run_space", 40),
])
def test_case_counts_per_protocol(protocol, expected):
    cases = generate_universal_cases(TAX_CONTRACT, protocol, seed=1)
    assert len(cases) == expected


def test_no_input_static_has_one_empty_case():
    cases = generate_universal_cases(STATIC_CONTRACT, "no_input_static")
    assert [c.inputs for c in cases] == [{}]


def test_cases_cover_bounds_zero_cent_negative_and_half_cent():
    cases = generate_universal_cases(
        {"inputs": [{"name": "x", "pic": "S9(5)V999", "scale": 3, "min": "-100.000", "max": "500.000"}],
         "outputs": [{"name": "y", "scale": 3}]},
        "single_run_lines", seed=2)
    values = {c.inputs["x"] for c in cases}
    assert Decimal("-100.000") in values and Decimal("500.000") in values
    assert Decimal("0.000") in values and Decimal("0.010") in values
    assert Decimal("-0.010") in values
    assert any(v % Decimal("0.01") != 0 and (v * 1000) % 10 == 5 for v in values)  # x.xx5


@settings(max_examples=30, deadline=None)
@given(lo=st.integers(-5000, 0), hi=st.integers(1, 9999), seed=st.integers(0, 1000))
def test_cases_stay_inside_declared_bounds(lo, hi, seed):
    contract = {"inputs": [{"name": "a", "pic": "S9(4)V99", "scale": 2,
                            "min": str(Decimal(lo).scaleb(-2)), "max": str(Decimal(hi).scaleb(-2))}],
                "outputs": [{"name": "o", "scale": 2}]}
    for c in generate_universal_cases(contract, "batch_space", 60, seed):
        assert Decimal(lo).scaleb(-2) <= c.inputs["a"] <= Decimal(hi).scaleb(-2)


# -- real GnuCOBOL tests ------------------------------------------------------

@needs_runtime
def test_batch_space_loan_calc_reaches_full_parity():
    bundles = json.loads((SAMPLES / "preset_bundles.json").read_text(encoding="utf-8"))
    code = bundles["loan_calc"]["false"]["verified_python_code"]  # Test_Oracle use only
    contract = {
        "inputs": [
            {"name": "principal", "pic": "9(9)V99", "scale": 2, "min": "0.01", "max": "10000000.00"},
            {"name": "annual_rate_pct", "pic": "9(3)V999", "scale": 3, "min": "0.000", "max": "100.000"},
            {"name": "term_months", "pic": "9(3)", "scale": 0, "min": "1", "max": "360"},
        ],
        "outputs": [{"name": n, "scale": 2} for n in
                    ("monthly_payment", "total_interest", "total_payable")],
    }
    source = (SAMPLES / "loan_calc.cbl").read_text(encoding="utf-8")
    report = run_universal_verification(source, contract, "batch_space", code, seed=7)
    assert report.total_cases == 150
    assert report.verified and report.parity_score_pct == "100.00"


@needs_runtime
def test_single_run_lines_reaches_parity_with_one_compile(monkeypatch):
    compiles = []
    runner = pbt_verifier.CobolRunner()
    real = runner.compile_source
    monkeypatch.setattr(runner, "compile_source", lambda *a, **k: compiles.append(1) or real(*a, **k))
    report = run_universal_verification(
        TAX_COBOL, TAX_CONTRACT, "single_run_lines", TAX_PY, seed=3, runner=runner)
    assert report.verified, report.counterexamples
    assert 35 <= report.total_cases <= 50
    assert len(compiles) == 1


@needs_runtime
def test_no_input_static_runs_once_and_matches():
    report = run_universal_verification(
        STATIC_COBOL, STATIC_CONTRACT, "no_input_static",
        "def calculate():\n    return {'result': Decimal('12.50')}\n")
    assert report.total_cases == 1 and report.verified


@needs_runtime
def test_wrong_python_yields_counterexample_with_field():
    py = TAX_PY.replace("ROUND_DOWN", "ROUND_HALF_UP")
    report = run_universal_verification(TAX_COBOL, TAX_CONTRACT, "single_run_lines", py, seed=3)
    assert not report.verified
    ce = report.counterexamples[0]
    assert ce.differing_field == "tax"
    assert set(ce.inputs) == {"gross", "rate"} and ce.cobol_output and ce.python_output


@needs_runtime
def test_float_demo_measures_exact_drift():
    report = run_universal_verification(
        TAX_COBOL, TAX_CONTRACT, "single_run_lines", TAX_FLOAT_PY, seed=3, allow_float_demo=True)
    assert not report.verified
    ce = report.counterexamples[0]
    assert ce.likely_cause == "float_drift"
    assert Decimal(ce.penny_delta) != 0


@needs_runtime
def test_float_code_without_demo_flag_is_python_error():
    report = run_universal_verification(
        TAX_COBOL, TAX_CONTRACT, "single_run_lines", TAX_FLOAT_PY, seed=3)
    assert not report.verified
    assert report.counterexamples[0].likely_cause == "python_error"
