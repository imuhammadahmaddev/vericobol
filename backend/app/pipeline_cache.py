"""In-memory SHA-256 keyed LRU cache for Resurrection_Pipeline results.

Holds at most 64 entries. Starts empty (never seeded); callers ``put`` only\nCertified results produced by live LLM stages. Keys are SHA-256 hex digests of the normalized COBOL
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
    # Inferred program (contract, protocol, title) and the float demo code.
    extra: dict[str, Any] = field(default_factory=dict)


def _fold_line(line: str) -> str:
    """Collapse whitespace and uppercase everything outside string literals.

    Inline ``*>`` comments are dropped. Quote state is tracked per line.
    """
    out: list[str] = []
    quote = ""
    pending_space = False
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if quote:
            out.append(ch)
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            if pending_space and out:
                out.append(" ")
            pending_space = False
            quote = ch
            out.append(ch)
        elif ch.isspace():
            pending_space = True
        elif ch == "*" and line.startswith("*>", i):
            break
        else:
            if pending_space and out:
                out.append(" ")
            pending_space = False
            out.append(ch.upper())
        i += 1
    return "".join(out)


def _is_comment(line: str) -> bool:
    stripped = line.lstrip()
    if stripped.startswith("*"):
        return True
    # Fixed format: ``*`` in column 7 with a sequence area of digits/spaces.
    return len(line) > 6 and line[6] == "*" and all(c.isdigit() or c == " " for c in line[:6])


def _normalize_once(source: str) -> str:
    text = source.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for raw in text.split("\n"):
        if _is_comment(raw.rstrip()):
            continue
        folded = _fold_line(raw)
        if folded:
            lines.append(folded)
    return "\n".join(lines)


def normalize_cobol(source: str) -> str:
    """Canonical form used for cache keys. Idempotent.

    Normalizes line endings, drops comment lines (``*`` in column 7, leading
    ``*``/``*>``) and inline ``*>`` comments, collapses whitespace, uppercases
    keywords outside string literals, and removes blank lines.
    """
    current = _normalize_once(source)
    while True:  # fixpoint guarantees idempotence
        nxt = _normalize_once(current)
        if nxt == current:
            return current
        current = nxt

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
