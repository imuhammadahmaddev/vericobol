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


# --------------------------------------------------------------------------
# Deterministic LLM stub (resurrection-pipeline, Requirement 12.2)
# --------------------------------------------------------------------------
import random  # noqa: E402
from typing import Any  # noqa: E402

from app.pipeline_cache import PipelineCache  # noqa: E402
from app.resurrection import ArchitectOutput, ParsedRules  # noqa: E402

_STUB_CODE = (
    "from decimal import Decimal\n\n"
    "def calculate(**kwargs):\n"
    "    return {}\n"
)


class StubLLMClient:
    """Test double for ``app.llm_client.LLMClient``. Never touches the network.

    Outputs come from scripts (lists consumed in order, last item repeats) or,
    when a ``seed`` is given and no script is set, from a seeded RNG. Every
    method increments ``calls[<name>]``; ``call_log`` keeps the arguments.
    """

    def __init__(
        self,
        *,
        rules: list[ParsedRules] | ParsedRules | None = None,
        generated: list[ArchitectOutput] | ArchitectOutput | None = None,
        repaired: list[ArchitectOutput] | ArchitectOutput | None = None,
        seed: int | None = None,
        raises: Exception | None = None,
    ) -> None:
        self._scripts: dict[str, list[Any]] = {
            "extract_rules": self._as_list(rules),
            "generate_python": self._as_list(generated),
            "repair_python": self._as_list(repaired),
        }
        self._rng = random.Random(seed if seed is not None else 0)
        self._raises = raises
        self.calls: dict[str, int] = {
            "extract_rules": 0,
            "generate_python": 0,
            "repair_python": 0,
        }
        self.call_log: list[tuple[str, tuple[Any, ...]]] = []

    @staticmethod
    def _as_list(value: Any) -> list[Any]:
        if value is None:
            return []
        return list(value) if isinstance(value, (list, tuple)) else [value]

    @property
    def call_count(self) -> int:
        return sum(self.calls.values())

    def _next(self, name: str, default: Any, args: tuple[Any, ...]) -> Any:
        self.calls[name] += 1
        self.call_log.append((name, args))
        if self._raises is not None:
            raise self._raises
        script = self._scripts[name]
        if script:
            index = min(self.calls[name] - 1, len(script) - 1)
            return script[index]
        return default

    def require_key(self) -> None:
        self.key_checks = getattr(self, "key_checks", 0) + 1

    def extract_rules(self, cobol_source: str) -> ParsedRules:
        n = self._rng.randint(1, 4)
        default = ParsedRules(
            business_rules=[f"Stub rule {i}" for i in range(n)], pic_mappings=[]
        )
        return self._next("extract_rules", default, (cobol_source,))

    def generate_python(self, cobol_source, rules, schema) -> ArchitectOutput:
        default = ArchitectOutput(
            verified_python_code=_STUB_CODE, dashboard_metadata={}
        )
        return self._next("generate_python", default, (cobol_source, rules, schema))

    def repair_python(
        self, cobol_source, rules, schema, counterexample, previous_code
    ) -> ArchitectOutput:
        default = ArchitectOutput(
            verified_python_code=_STUB_CODE, dashboard_metadata={}
        )
        return self._next(
            "repair_python",
            default,
            (cobol_source, rules, schema, counterexample, previous_code),
        )


@pytest.fixture
def stub_llm_factory():
    """Return the StubLLMClient class so tests can build scripted stubs."""
    return StubLLMClient


@pytest.fixture
def stub_llm() -> StubLLMClient:
    """A default, randomized-but-seeded stub with a call counter."""
    return StubLLMClient(seed=0)


@pytest.fixture
def pipeline_cache() -> PipelineCache:
    """A fresh, empty PipelineCache per test."""
    return PipelineCache()


@pytest.fixture
def no_llm_api_key(monkeypatch):
    """Clear LLM_API_KEY so any accidental real client has no key."""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    return None


@pytest.fixture
def injected_llm(stub_llm, pipeline_cache, no_llm_api_key):
    """Bundle: (stub LLM, fresh cache) with the API key cleared."""
    return stub_llm, pipeline_cache
