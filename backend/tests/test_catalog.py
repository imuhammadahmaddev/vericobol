"""Unit tests for the preset catalog shape and repository setup rules."""

from __future__ import annotations

import json
import os
import re
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from .conftest import BACKEND_DIR, PRESET_IDS, SAMPLES_DIR

PRESET_KEYS = {"preset_id", "name", "description", "source_file", "input_schema", "sample_input"}
FIELD_KEYS = {"name", "label", "min", "max", "step"}
LEDGER_ORDER = [
    "opening_balance",
    "deposits",
    "withdrawals",
    "overdraft_threshold",
    "overdraft_fee",
]
BANNED_LIBS = ("pandas", "numpy", "scipy", "torch")


@pytest.fixture(scope="module")
def catalog() -> dict:
    with (SAMPLES_DIR / "presets.json").open(encoding="utf-8") as fh:
        return json.load(fh)


def _repo_root() -> Path | None:
    env = os.environ.get("VERICOBOL_REPO_ROOT")
    candidates = [Path(env)] if env else []
    candidates += [Path("/repo"), BACKEND_DIR.parent]
    for c in candidates:
        if (c / ".gitattributes").is_file():
            return c
    return None


def test_three_entries(catalog):
    assert set(catalog) == set(PRESET_IDS)
    assert len(catalog) == 3


@pytest.mark.parametrize("preset_id", PRESET_IDS)
def test_required_keys(catalog, preset_id):
    entry = catalog[preset_id]
    assert PRESET_KEYS <= set(entry)
    assert entry["preset_id"] == preset_id
    assert (SAMPLES_DIR / entry["source_file"]).is_file()
    assert entry["input_schema"]
    for field in entry["input_schema"]:
        assert FIELD_KEYS <= set(field)


@pytest.mark.parametrize("preset_id", PRESET_IDS)
def test_money_values_are_strings(catalog, preset_id):
    entry = catalog[preset_id]
    for field in entry["input_schema"]:
        for key in ("min", "max", "step", "default"):
            if key in field:
                assert isinstance(field[key], str), (field["name"], key)
                Decimal(field[key])
    for name, value in entry["sample_input"].items():
        assert isinstance(value, str), name
        Decimal(value)


@pytest.mark.parametrize("preset_id", PRESET_IDS)
def test_sample_input_within_bounds(catalog, preset_id):
    entry = catalog[preset_id]
    names = [f["name"] for f in entry["input_schema"]]
    assert set(entry["sample_input"]) == set(names)
    for field in entry["input_schema"]:
        value = Decimal(entry["sample_input"][field["name"]])
        assert Decimal(field["min"]) <= value <= Decimal(field["max"]), field["name"]


def test_daily_ledger_field_order_and_default(catalog):
    entry = catalog["daily_ledger"]
    assert [f["name"] for f in entry["input_schema"]] == LEDGER_ORDER
    threshold = next(f for f in entry["input_schema"] if f["name"] == "overdraft_threshold")
    assert threshold["default"] == "0.00"
    assert entry["sample_input"]["overdraft_threshold"] is not None
    assert "overdraft_threshold" in entry["sample_input"]


def test_gitattributes_rules():
    root = _repo_root()
    if root is None:
        pytest.skip(".gitattributes not available (set VERICOBOL_REPO_ROOT or mount at /repo)")
    lines = {
        ln.strip()
        for ln in (root / ".gitattributes").read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.startswith("#")
    }
    for pattern in ("*.cbl", "*.py", "*.sh", "Dockerfile"):
        assert f"{pattern} text eol=lf" in lines


def test_requirements_exclude_banned_libs():
    text = (BACKEND_DIR / "requirements.txt").read_text(encoding="utf-8")
    names = {
        re.split(r"[=<>~!\[;\s]", ln.strip(), maxsplit=1)[0].lower()
        for ln in text.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    }
    for lib in BANNED_LIBS:
        assert lib not in names


@pytest.mark.parametrize("preset_id", PRESET_IDS)
def test_preset_compiles_and_runs_sample(catalog, require_preset, preset_id):
    binary = require_preset(preset_id)
    entry = catalog[preset_id]
    row = " ".join(entry["sample_input"][f["name"]] for f in entry["input_schema"])
    proc = subprocess.run(
        [str(binary)],
        input=row + "\n",
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, proc.stdout
