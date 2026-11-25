"""In-memory SHA-256 keyed LRU cache for Resurrection_Pipeline results.

Holds at most 64 entries. Keys are SHA-256 hex digests of the normalized COBOL
source plus a one-byte demonstrate_self_healing flag. No I/O, no floats.
"""
from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

MAX_ENTRIES = 64


@dataclass(frozen=True)
class CachedResult:
    """Pipeline output stored per cache key (everything except the fresh parity report)."""

    business_rules: list[str]
    pic_mappings: list[dict[str, str]]
    dashboard_metadata: dict[str, Any]
    verified_python_code: str
    export_fastapi_code: str
    # StageRecord objects (defined in resurrection.py) or their dict form.
    stages: list[Any] = field(default_factory=list)


def normalize_cobol(source: str) -> str:
    """Convert line endings to ``\\n``, strip trailing whitespace on each line,
    and remove leading and trailing blank lines. Idempotent."""
    text = source.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    start = 0
    end = len(lines)
    while start < end and lines[start] == "":
        start += 1
    while end > start and lines[end - 1] == "":
        end -= 1
    return "\n".join(lines[start:end])


def cache_key(cobol_source: str, demonstrate_self_healing: bool) -> str:
    """SHA-256 hex digest of normalized source followed by a demo-flag byte."""
    payload = normalize_cobol(cobol_source).encode("utf-8")
    payload += b"\x01" if demonstrate_self_healing else b"\x00"
    return hashlib.sha256(payload).hexdigest()


class PipelineCache:
    """Thread-safe LRU keyed on SHA-256 hex digests."""

    def __init__(self, max_entries: int = MAX_ENTRIES) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._max_entries = max_entries
        self._data: OrderedDict[str, CachedResult] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> CachedResult | None:
        with self._lock:
            value = self._data.get(key)
            if value is None:
                return None
            self._data.move_to_end(key)
            return value

    def put(self, key: str, value: CachedResult) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self._max_entries:
                self._data.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return key in self._data
