"""Static preset catalog, loaded once per process and served from memory."""

from __future__ import annotations

import json
import threading
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi.responses import Response

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "cobol_samples"
CATALOG_PATH = SAMPLES_DIR / "presets.json"
PRESET_ORDER = ("loan_calc", "compound_int", "daily_ledger")

_lock = threading.Lock()
_PRESETS: list[dict[str, Any]] | None = None


def _json_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Unserializable type: {type(value).__name__}")


def _stringify(value: Any) -> Any:
    """Convert any Decimal (or stray float) to its string form, recursively."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float):
        return str(Decimal(repr(value)))
    if isinstance(value, dict):
        return {k: _stringify(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_stringify(v) for v in value]
    return value


def load_presets() -> list[dict[str, Any]]:
    """Return the three presets (id, name, description, source, schema, sample)."""
    global _PRESETS
    if _PRESETS is None:
        with _lock:
            if _PRESETS is None:
                with CATALOG_PATH.open("r", encoding="utf-8") as fh:
                    raw = json.load(fh, parse_float=Decimal)
                presets: list[dict[str, Any]] = []
                for preset_id in PRESET_ORDER:
                    entry = raw[preset_id]
                    source = (SAMPLES_DIR / entry["source_file"]).read_text(encoding="utf-8")
                    presets.append(
                        _stringify(
                            {
                                "preset_id": entry["preset_id"],
                                "name": entry["name"],
                                "description": entry["description"],
                                "cobol_source": source,
                                "input_schema": entry["input_schema"],
                                "sample_input": entry["sample_input"],
                            }
                        )
                    )
                _PRESETS = presets
    return _PRESETS


class DecimalJSONResponse(Response):
    """JSON response that renders Decimal values as strings, never floats."""

    media_type = "application/json"

    def render(self, content: Any) -> bytes:
        return json.dumps(
            content, default=_json_default, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
