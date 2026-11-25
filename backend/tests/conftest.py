"""Shared pytest fixtures for the VeriCOBOL backend tests."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
SAMPLES_DIR = BACKEND_DIR / "cobol_samples"
PRESET_IDS = ("loan_calc", "compound_int", "daily_ledger")


@pytest.fixture(scope="session", autouse=True)
def _reference_model_oracle():
    """Install reference_models as the test-only oracle for run_verification."""
    from app import pbt_verifier, reference_models

    def provider(preset_id: str, simulate_float_bug: bool):
        registry = (reference_models.FLOAT_BUG_REGISTRY if simulate_float_bug
                    else reference_models.REGISTRY)
        return registry[preset_id]

    pbt_verifier.ORACLE_PROVIDER = provider
    yield
    pbt_verifier.ORACLE_PROVIDER = None


@pytest.fixture(scope="session")
def cobc_path() -> str:
    return os.environ.get("COBC_PATH", "cobc")


@pytest.fixture(scope="session")
def samples_dir() -> Path:
    return SAMPLES_DIR


def _compile(cobc: str, source: Path, out_dir: Path) -> Path:
    binary = out_dir / source.stem
    subprocess.run(
        [cobc, "-x", "-free", "-o", str(binary), str(source)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return binary


@pytest.fixture(scope="module")
def compiled_presets(cobc_path: str, samples_dir: Path):
    """Compile each existing preset once per test module.

    Returns dict[preset_id, Path]. Presets whose .cbl file does not exist yet
    are omitted, so tests can skip when their preset is missing.
    """
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        binaries: dict[str, Path] = {}
        for preset_id in PRESET_IDS:
            source = samples_dir / f"{preset_id}.cbl"
            if source.is_file():
                binaries[preset_id] = _compile(cobc_path, source, out_dir)
        yield binaries


@pytest.fixture
def require_preset(compiled_presets):
    """Return a callable that gives the binary path or skips if missing."""

    def _get(preset_id: str) -> Path:
        if preset_id not in compiled_presets:
            pytest.skip(f"preset {preset_id} not available yet")
        return compiled_presets[preset_id]

    return _get



# --------------------------------------------------------------------------
# Universal-pipeline test doubles
#
# ``preset_bundles.json`` and ``app.reference_models`` are Test_Oracles only:
# the runtime never reads them. Tests use the bundled Decimal code as the
# "LLM output" of a scripted fake, and the real GnuCOBOL binary as the judge.
# --------------------------------------------------------------------------
import json  # noqa: E402
from typing import Any  # noqa: E402

from app.llm_client import InferredProgram  # noqa: E402
from app.pipeline_cache import PipelineCache  # noqa: E402
from app.resurrection import ArchitectOutput  # noqa: E402

BAD_PYTHON = (
    "def calculate(**kwargs):\n"
    "    return {'monthly_payment': Decimal('0.00'), 'total_interest': Decimal('0.00'),"
    " 'total_payable': Decimal('0.00')}\n"
)


def preset_source(preset_id: str) -> str:
    return (SAMPLES_DIR / f"{preset_id}.cbl").read_text(encoding="utf-8")


def oracle_python(preset_id: str) -> str:
    """Decimal Python from preset_bundles.json (a test-only oracle)."""
    bundles = json.loads((SAMPLES_DIR / "preset_bundles.json").read_text(encoding="utf-8"))
    return bundles[preset_id]["false"]["verified_python_code"]


# name, label, PIC, scale, rounding (outputs only)
_PRESET_FIELDS: dict[str, dict[str, list[tuple[Any, ...]]]] = {
    "loan_calc": {
        "inputs": [
            ("principal", "9(9)V99", 2),
            ("annual_rate_pct", "9(3)V999", 3),
            ("term_months", "9(3)", 0),
        ],
        "outputs": [
            ("monthly_payment", "9(9)V99", 2, "ROUND_HALF_UP", "currency"),
            ("total_interest", "9(9)V99", 2, "ROUND_HALF_UP", "currency"),
            ("total_payable", "9(9)V99", 2, "ROUND_HALF_UP", "currency"),
        ],
    },
    "compound_int": {
        "inputs": [
            ("principal", "9(9)V99", 2),
            ("annual_rate_pct", "9(3)V999", 3),
            ("compounds_per_year", "9(2)", 0),
            ("years", "9(2)", 0),
        ],
        "outputs": [
            ("maturity_amount", "9(9)V99", 2, "ROUND_HALF_UP", "currency"),
            ("accrued_interest", "S9(9)V99", 2, "ROUND_HALF_UP", "currency"),
        ],
    },
    "daily_ledger": {
        "inputs": [
            ("opening_balance", "S9(9)V99", 2),
            ("deposits", "9(9)V99", 2),
            ("withdrawals", "9(9)V99", 2),
            ("overdraft_threshold", "S9(9)V99", 2),
            ("overdraft_fee", "9(5)V99", 2),
        ],
        "outputs": [
            ("closing_balance", "S9(9)V99", 2, "ROUND_DOWN", "currency"),
            ("fee_applied", "9(5)V99", 2, "ROUND_DOWN", "currency"),
            ("overdraft_flag", "9(1)", 0, "ROUND_DOWN", "flag"),
        ],
    },
}


def preset_program(preset_id: str) -> InferredProgram:
    """The contract an LLM would infer for a starter template (bounds from presets.json)."""
    catalog = json.loads((SAMPLES_DIR / "presets.json").read_text(encoding="utf-8"))[preset_id]
    bounds = {f["name"]: f for f in catalog["input_schema"]}
    spec = _PRESET_FIELDS[preset_id]
    return InferredProgram(
        program_title=catalog["name"],
        description=catalog["description"],
        io_protocol="batch_space",
        contract={
            "inputs": [
                {"name": n, "label": bounds[n]["label"], "pic": pic, "scale": scale,
                 "min": bounds[n]["min"], "max": bounds[n]["max"], "step": bounds[n]["step"]}
                for n, pic, scale in spec["inputs"]
            ],
            "outputs": [
                {"name": n, "label": n, "pic": pic, "scale": scale, "rounding": rnd, "format": fmt}
                for n, pic, scale, rnd, fmt in spec["outputs"]
            ],
        },
        business_rules=[f"Rule for {preset_id}."],
        dashboard_metadata={"title": catalog["name"], "chart_type": "summary"},
    )


class ScriptedLLM:
    """Scripted stand-in for ``LLMClient``. No network, no tokens.

    ``generated`` and ``repaired`` are code strings or lists of them (consumed
    in order, last item repeats). ``unavailable`` / ``raises`` make the matching
    call raise. ``calls`` counts every method; ``call_log`` keeps the arguments.
    """

    def __init__(
        self,
        program: InferredProgram | None = None,
        *,
        generated: str | list[str] | None = None,
        repaired: str | list[str] | None = None,
        unavailable: Exception | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.program = program
        self._scripts = {
            "generate": [generated] if isinstance(generated, str) else list(generated or []),
            "repair": [repaired] if isinstance(repaired, str) else list(repaired or []),
        }
        self._unavailable = unavailable
        self._raises = raises
        self.calls = {"infer": 0, "generate": 0, "repair": 0}
        self.call_log: list[tuple[str, tuple[Any, ...]]] = []

    @property
    def call_count(self) -> int:
        return sum(self.calls.values())

    def _code(self, name: str, args: tuple[Any, ...]) -> ArchitectOutput:
        self.calls[name] += 1
        self.call_log.append((name, args))
        if self._raises is not None:
            raise self._raises
        script = self._scripts[name]
        return ArchitectOutput(script[min(self.calls[name] - 1, len(script) - 1)], {})

    def require_key(self) -> None:
        if self._unavailable is not None:
            raise self._unavailable

    def infer_contract(self, cobol_source, validation_error=None) -> InferredProgram:
        self.calls["infer"] += 1
        self.call_log.append(("infer", (cobol_source,)))
        if self._raises is not None:
            raise self._raises
        assert self.program is not None, "ScriptedLLM needs a program to infer"
        return self.program

    def generate_python(self, cobol_source, program, *, float_demo=False) -> ArchitectOutput:
        return self._code("generate", (cobol_source, program, float_demo))

    def repair_python(self, cobol_source, program, counterexample, previous_code) -> ArchitectOutput:
        return self._code("repair", (cobol_source, program, counterexample, previous_code))


@pytest.fixture
def pipeline_cache() -> PipelineCache:
    """A fresh, empty PipelineCache per test (the real cache starts unseeded)."""
    return PipelineCache()
