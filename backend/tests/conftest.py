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
