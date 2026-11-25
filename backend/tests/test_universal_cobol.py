"""Universal pipeline tests: scripted LLM, real GnuCOBOL (task 10.1).

Covers a Payroll Tax Calculator in ``single_run_lines`` and a fixed-format
80-column program in ``single_run_space``. The LLM is ``ScriptedLLM`` from
conftest: no network, 0 tokens. Needs ``cobc`` and ``fork`` (Docker image).
"""

from __future__ import annotations

import multiprocessing
import shutil

import pytest

from app.cobol_runner import CobolRunner
from app.llm_client import InferredProgram
from app.pipeline_cache import PipelineCache
from app.resurrection import Pipeline

from .conftest import ScriptedLLM

needs_runtime = pytest.mark.skipif(
    shutil.which("cobc") is None or "fork" not in multiprocessing.get_all_start_methods(),
    reason="needs cobc and fork (use the Docker image)",
)

# ---------------------------------------------------------------------------
# Payroll Tax Calculator (free format, one ACCEPT per input field)
# ---------------------------------------------------------------------------

PAYROLL_SOURCE = """\
*> Payroll Tax Calculator (single_run_lines).
*> stdin lines: gross pay, tax rate percent, deductions.
IDENTIFICATION DIVISION.
PROGRAM-ID. PAYROLL-TAX.

DATA DIVISION.
WORKING-STORAGE SECTION.
01 WS-IN-GROSS     PIC X(40).
01 WS-IN-RATE      PIC X(40).
01 WS-IN-DED       PIC X(40).
01 WS-GROSS        PIC 9(7)V99.
01 WS-RATE         PIC 9(2)V99.
01 WS-DED          PIC 9(5)V99.
01 WS-TAX          PIC 9(7)V99.
01 WS-NET          PIC S9(7)V99.
01 WS-ED-TAX       PIC Z(6)9.99.
01 WS-ED-NET       PIC -(7)9.99.

PROCEDURE DIVISION.
MAIN-PARA.
    ACCEPT WS-IN-GROSS FROM SYSIN
    ACCEPT WS-IN-RATE FROM SYSIN
    ACCEPT WS-IN-DED FROM SYSIN
    COMPUTE WS-GROSS = FUNCTION NUMVAL(WS-IN-GROSS)
    COMPUTE WS-RATE = FUNCTION NUMVAL(WS-IN-RATE)
    COMPUTE WS-DED = FUNCTION NUMVAL(WS-IN-DED)
    COMPUTE WS-TAX ROUNDED = WS-GROSS * WS-RATE / 100
    COMPUTE WS-NET = WS-GROSS - WS-TAX - WS-DED
    MOVE WS-TAX TO WS-ED-TAX
    MOVE WS-NET TO WS-ED-NET
    DISPLAY "Tax: " FUNCTION TRIM(WS-ED-TAX)
    DISPLAY "Net Pay: " FUNCTION TRIM(WS-ED-NET)
    STOP RUN.
"""

PAYROLL_PYTHON = """
def calculate(**kw):
    gross = Decimal(kw["gross_pay"])
    rate = Decimal(kw["tax_rate_pct"])
    ded = Decimal(kw["deductions"])
    tax = (gross * rate / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {"tax": tax, "net_pay": gross - tax - ded}
"""


def _input(name: str, pic: str, scale: int, lo: str, hi: str) -> dict:
    return {"name": name, "label": name, "pic": pic, "scale": scale,
            "min": lo, "max": hi, "step": None}


def _output(name: str, pic: str, scale: int, rounding: str, fmt: str = "currency") -> dict:
    return {"name": name, "label": name, "pic": pic, "scale": scale,
            "rounding": rounding, "format": fmt}


PAYROLL_PROGRAM = InferredProgram(
    program_title="Payroll Tax Calculator",
    description="Withholds tax on gross pay and returns net pay.",
    io_protocol="single_run_lines",
    contract={
        "inputs": [
            _input("gross_pay", "9(7)V99", 2, "0.00", "100000.00"),
            _input("tax_rate_pct", "9(2)V99", 2, "0.00", "50.00"),
            _input("deductions", "9(5)V99", 2, "0.00", "5000.00"),
        ],
        "outputs": [
            _output("tax", "9(7)V99", 2, "ROUND_HALF_UP"),
            _output("net_pay", "S9(7)V99", 2, "ROUND_DOWN"),
        ],
    },
    business_rules=["Tax is gross pay times the rate, rounded half up to the cent."],
    dashboard_metadata={"title": "Payroll", "chart_type": "summary", "inputs": [], "outputs": []},
)

# ---------------------------------------------------------------------------
# Fixed-format 80-column program (sequence numbers in columns 1-6, comment in
# column 7, code from column 8, identification area in columns 73-80).
# ---------------------------------------------------------------------------

_FIXED_BODY = [
    ("*", "SIMPLE INTEREST (FIXED FORMAT, 80 COLUMNS)"),
    (" ", "IDENTIFICATION DIVISION."),
    (" ", "PROGRAM-ID. SIMPLE-INT."),
    (" ", "DATA DIVISION."),
    (" ", "WORKING-STORAGE SECTION."),
    (" ", "01 WS-LINE      PIC X(100)."),
    (" ", "01 WS-TOK-AMT   PIC X(40)."),
    (" ", "01 WS-TOK-RATE  PIC X(40)."),
    (" ", "01 WS-AMT       PIC 9(5)V99."),
    (" ", "01 WS-RATE      PIC 9(2)V99."),
    (" ", "01 WS-INTEREST  PIC 9(5)V99."),
    (" ", "01 WS-TOTAL     PIC 9(6)V99."),
    (" ", "01 WS-ED-INT    PIC Z(4)9.99."),
    (" ", "01 WS-ED-TOT    PIC Z(5)9.99."),
    (" ", "PROCEDURE DIVISION."),
    (" ", "MAIN-PARA."),
    (" ", "    ACCEPT WS-LINE FROM SYSIN"),
    (" ", "    UNSTRING FUNCTION TRIM(WS-LINE) DELIMITED BY ALL SPACE"),
    (" ", "        INTO WS-TOK-AMT WS-TOK-RATE"),
    (" ", "    END-UNSTRING"),
    (" ", "    COMPUTE WS-AMT = FUNCTION NUMVAL(WS-TOK-AMT)"),
    (" ", "    COMPUTE WS-RATE = FUNCTION NUMVAL(WS-TOK-RATE)"),
    (" ", "    COMPUTE WS-INTEREST ROUNDED = WS-AMT * WS-RATE / 100"),
    (" ", "    COMPUTE WS-TOTAL = WS-AMT + WS-INTEREST"),
    (" ", "    MOVE WS-INTEREST TO WS-ED-INT"),
    (" ", "    MOVE WS-TOTAL TO WS-ED-TOT"),
    (" ", "    DISPLAY FUNCTION TRIM(WS-ED-INT) \" \" FUNCTION TRIM(WS-ED-TOT)"),
    (" ", "    STOP RUN."),
]


def _fixed_source() -> str:
    lines = []
    for n, (indicator, code) in enumerate(_FIXED_BODY, start=1):
        text = f"{n * 100:06d}{indicator}{code}"
        assert len(text) <= 72, text
        lines.append(f"{text:<72}FIXEDFMT")      # columns 73-80: identification area
    assert all(len(line) == 80 for line in lines)
    return "\n".join(lines) + "\n"


FIXED_SOURCE = _fixed_source()

FIXED_PYTHON = """
def calculate(**kw):
    amount = Decimal(kw["amount"])
    rate = Decimal(kw["rate_pct"])
    interest = (amount * rate / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {"interest": interest, "total": amount + interest}
"""

FIXED_PROGRAM = InferredProgram(
    program_title="Simple Interest",
    description="Simple interest on an amount, fixed-format source.",
    io_protocol="single_run_space",
    contract={
        "inputs": [
            _input("amount", "9(5)V99", 2, "0.00", "99999.99"),
            _input("rate_pct", "9(2)V99", 2, "0.00", "99.99"),
        ],
        "outputs": [
            _output("interest", "9(5)V99", 2, "ROUND_HALF_UP"),
            _output("total", "9(6)V99", 2, "ROUND_DOWN"),
        ],
    },
    business_rules=["Interest is amount times rate percent, rounded half up."],
    dashboard_metadata={"title": "Simple Interest", "chart_type": "summary", "inputs": [], "outputs": []},
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def _assert_full_parity(resp) -> None:
    report = resp.final_report.report
    assert resp.certified
    assert 35 <= report.total_cases <= 50
    assert report.passed_cases == report.total_cases
    assert report.failed_cases == 0
    assert report.parity_score_pct == "100.00"
    assert report.counterexamples == []
    assert report.raw_cobol_sample_output  # real GnuCOBOL output was parsed


@needs_runtime
def test_payroll_single_run_lines_reaches_full_parity():
    llm = ScriptedLLM(PAYROLL_PROGRAM, generated=PAYROLL_PYTHON)
    resp = Pipeline(llm, PipelineCache()).run(PAYROLL_SOURCE)

    _assert_full_parity(resp)
    assert llm.calls == {"infer": 1, "generate": 1, "repair": 0}
    assert [s.source for s in resp.stages] == ["llm", "llm", "live"]
    assert [i["name"] for i in resp.contract["inputs"]] == ["gross_pay", "tax_rate_pct", "deductions"]
    assert [o["name"] for o in resp.contract["outputs"]] == ["tax", "net_pay"]
    assert len(resp.audit_trail) == 1


PAYROLL_SCHEMA = {**PAYROLL_PROGRAM.contract}


def _payroll_body() -> dict:
    return {
        "cobol_code": PAYROLL_SOURCE,
        "python_code": PAYROLL_PYTHON,
        "schema": PAYROLL_SCHEMA,
        "io_protocol": "single_run_lines",
        "inputs": {"gross_pay": "1234.56", "tax_rate_pct": "7.25", "deductions": "100.00"},
    }


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app import main

    return TestClient(main.app)


@needs_runtime
def test_calculate_custom_end_to_end_matches_cobol(client):
    r = client.post("/api/calculate-custom", json=_payroll_body())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["parity_verified"] is True
    assert data["result"] == {"tax": "89.51", "net_pay": "1045.05"}
    assert "Tax:" in data["raw_cobol_output"]


@needs_runtime
def test_calculate_custom_rejects_out_of_range_input(client):
    body = _payroll_body()
    body["inputs"]["tax_rate_pct"] = "99.00"
    r = client.post("/api/calculate-custom", json=body)
    assert r.status_code == 422
    assert r.json()["detail"]["field"] == "tax_rate_pct"


@needs_runtime
def test_export_custom_recertifies_live_with_real_cobol(client):
    body = {
        "program_title": "Payroll Tax Calculator",
        "python_code": PAYROLL_PYTHON,
        "schema": PAYROLL_SCHEMA,
        "cobol_code": PAYROLL_SOURCE,
        "io_protocol": "single_run_lines",
    }
    r = client.post("/api/export-custom", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["certified"] is True
    assert data["filename"] == "payroll_tax_calculator_api.py"
    assert "FastAPI" in data["export_fastapi_code"]

    bad = {**body, "python_code": PAYROLL_PYTHON.replace("ROUND_HALF_UP", "ROUND_DOWN")}
    r = client.post("/api/export-custom", json=bad)
    assert r.status_code == 200
    assert r.json()["certified"] is False


def test_call_system_is_rejected_400_with_zero_llm_calls(client, monkeypatch):
    from app import main

    llm = ScriptedLLM(PAYROLL_PROGRAM, generated=PAYROLL_PYTHON)
    monkeypatch.setattr(main, "_PIPELINE", Pipeline(llm, PipelineCache()))
    evil = PAYROLL_SOURCE.replace("STOP RUN.", 'CALL "SYSTEM" USING "id"\n    STOP RUN.')

    r = client.post("/api/resurrect", json={"cobol_code": evil})
    assert r.status_code == 400
    assert llm.call_count == 0

    body = {**_payroll_body(), "cobol_code": evil}
    assert client.post("/api/calculate-custom", json=body).status_code == 400


def test_llm_unavailable_is_503_without_bundle_fallback(client, monkeypatch):
    from app import main
    from app.llm_client import LLMUnavailable

    llm = ScriptedLLM(PAYROLL_PROGRAM, unavailable=None, raises=LLMUnavailable("missing_key"))
    monkeypatch.setattr(main, "_PIPELINE", Pipeline(llm, PipelineCache()))
    # a starter template must not be served from a bundle either
    r = client.post("/api/resurrect", json={"preset_id": "loan_calc"})
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "llm_unavailable"
    assert "final_report" not in r.json()


@needs_runtime
def test_fixed_format_80_column_program_reaches_full_parity():
    assert all(len(line) == 80 for line in FIXED_SOURCE.splitlines())
    runner = CobolRunner()
    compiled = runner.get_binary(FIXED_SOURCE)
    assert compiled.success, compiled.stderr
    assert compiled.source_format == "fixed"   # free-format attempt fails, fallback compiles

    llm = ScriptedLLM(FIXED_PROGRAM, generated=FIXED_PYTHON)
    resp = Pipeline(llm, PipelineCache(), runner).run(FIXED_SOURCE)

    _assert_full_parity(resp)
    assert llm.calls == {"infer": 1, "generate": 1, "repair": 0}
    assert [i["name"] for i in resp.contract["inputs"]] == ["amount", "rate_pct"]


# ---------------------------------------------------------------------------
# FX Wire calculator (batch_space) misclassified by the LLM as single_run_lines
# ---------------------------------------------------------------------------
FX_SOURCE = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. FX-WIRE.
*> FX Wire calculator (batch_space). Row: amount rate_pct fee
DATA DIVISION.
WORKING-STORAGE SECTION.
01 WS-LINE       PIC X(200).
01 WS-TOK-AMT    PIC X(40).
01 WS-TOK-RATE   PIC X(40).
01 WS-TOK-FEE    PIC X(40).
01 WS-EOF        PIC 9 VALUE 0.
01 WS-AMT        PIC 9(7)V99.
01 WS-RATE       PIC 9(3)V9999.
01 WS-FEE        PIC 9(5)V99.
01 WS-CONV       PIC 9(9)V99.
01 WS-NET        PIC 9(9)V99.
01 WS-OUT-CONV   PIC 9(9).99.
01 WS-OUT-NET    PIC 9(9).99.
PROCEDURE DIVISION.
MAIN-PARA.
    PERFORM UNTIL WS-EOF = 1
        MOVE SPACES TO WS-LINE
        ACCEPT WS-LINE
            ON EXCEPTION
                MOVE 1 TO WS-EOF
            NOT ON EXCEPTION
                PERFORM PROCESS-ROW
        END-ACCEPT
    END-PERFORM
    STOP RUN.
PROCESS-ROW.
    MOVE SPACES TO WS-TOK-AMT WS-TOK-RATE WS-TOK-FEE
    UNSTRING FUNCTION TRIM(WS-LINE) DELIMITED BY ALL SPACE
        INTO WS-TOK-AMT WS-TOK-RATE WS-TOK-FEE
    END-UNSTRING
    IF WS-TOK-FEE = SPACES
        EXIT PARAGRAPH
    END-IF
    COMPUTE WS-AMT = FUNCTION NUMVAL(WS-TOK-AMT)
    COMPUTE WS-RATE = FUNCTION NUMVAL(WS-TOK-RATE)
    COMPUTE WS-FEE = FUNCTION NUMVAL(WS-TOK-FEE)
    COMPUTE WS-CONV ROUNDED = WS-AMT * WS-RATE
    IF WS-CONV > WS-FEE
        COMPUTE WS-NET = WS-CONV - WS-FEE
    ELSE
        MOVE 0 TO WS-NET
    END-IF
    MOVE WS-CONV TO WS-OUT-CONV
    MOVE WS-NET TO WS-OUT-NET
    DISPLAY WS-OUT-CONV " " WS-OUT-NET.
"""
FX_PYTHON = """
def calculate(**kw):
    amount = Decimal(kw["amount"])
    rate = Decimal(kw["rate"])
    fee = Decimal(kw["fee"])
    conv = (amount * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    net = conv - fee if conv > fee else Decimal("0.00")
    return {"converted": conv, "net_payout": net}
"""
FX_MISCLASSIFIED = InferredProgram(
    program_title="FX Wire Calculator",
    description="Converts a wire amount and deducts a fee.",
    io_protocol="single_run_lines",  # deliberately wrong: the program is batch_space
    contract={
        "inputs": [
            _input("amount", "9(7)V99", 2, "0.00", "100000.00"),
            _input("rate", "9(3)V9999", 4, "0.0000", "50.0000"),
            _input("fee", "9(5)V99", 2, "0.00", "500.00"),
        ],
        "outputs": [
            _output("converted", "9(9)V99", 2, "ROUND_HALF_UP"),
            _output("net_payout", "9(9)V99", 2, "ROUND_DOWN"),
        ],
    },
    business_rules=["Converted amount is amount times rate, rounded half up."],
    dashboard_metadata={"title": "FX Wire", "chart_type": "summary", "inputs": [], "outputs": []},
)


@needs_runtime
def test_misclassified_batch_space_is_auto_corrected_by_probe():
    llm = ScriptedLLM(FX_MISCLASSIFIED, generated=FX_PYTHON)
    resp = Pipeline(llm, PipelineCache(), runner=CobolRunner()).run(FX_SOURCE)
    assert resp.io_protocol == "batch_space"
    assert resp.certified
    report = resp.final_report.report
    assert report.total_cases == 150
    assert report.passed_cases == 150
    assert report.parity_score_pct == "100.00"
