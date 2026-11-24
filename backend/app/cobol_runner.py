"""COBOL sandbox: static Security_Validator (and, in task 5.2, the CobolRunner).

``validate_source`` is a pure function of the source text. It never starts a
subprocess and never touches the file system.
"""

from __future__ import annotations

import atexit
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

MAX_SOURCE_BYTES = 64 * 1024
SUBPROCESS_TIMEOUT_S = 5.0
MAX_STDOUT_BYTES = 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
PRESETS_DIR = Path(__file__).resolve().parent.parent / "cobol_samples"
PRESET_IDS = frozenset({"loan_calc", "compound_int", "daily_ledger"})

# Characters that can be part of a COBOL user-defined word. Used so that a
# keyword inside an identifier (WS-READ-COUNT, END-READ, CALL-COUNT) is not a
# match, while "READ." or "READ\n" still is.
_NB = r"(?<![A-Z0-9_-])"
_NA = r"(?![A-Z0-9_-])"


def _kw(word: str) -> str:
    return _NB + word + _NA


@dataclass(frozen=True)
class ValidationError:
    token: str
    line: int
    message: str


@dataclass(frozen=True)
class ValidationResult:
    accepted: bool
    error: ValidationError | None


# Ordered, case-insensitive table: (token label or None to use matched text,
# regex, rationale). Patterns run against comment-free text in which string
# literal contents are blanked and whitespace (including line breaks) is
# collapsed, so a construct split across lines still matches. "[^.]*?" keeps a
# multi-word pattern inside one sentence. The bare word ENVIRONMENT is never
# matched, so "ENVIRONMENT DIVISION" is accepted without a separate allow-list.
_RULES: tuple[tuple[str | None, re.Pattern[str], str], ...] = tuple(
    (label, re.compile(pattern, re.IGNORECASE), why)
    for label, pattern, why in (
        ("CALL", _kw("CALL"), "arbitrary program invocation"),
        ("SYSTEM", _kw("SYSTEM"), "shell escape"),
        (
            "OPEN OUTPUT/EXTEND/I-O",
            _kw("OPEN") + r"[^.]*?" + _NB + r"(?:OUTPUT|EXTEND|I-O)" + _NA,
            "writable file I/O",
        ),
        (
            "SELECT ... ASSIGN",
            _kw("SELECT") + r"[^.]*?" + _kw("ASSIGN"),
            "file I/O",
        ),
        ("READ", _kw("READ"), "file I/O"),
        ("WRITE", _kw("WRITE"), "file I/O"),
        ("REWRITE", _kw("REWRITE"), "file I/O"),
        ("DELETE", _kw("DELETE"), "file I/O"),
        ("COPY", _kw("COPY"), "pulls in external files"),
        ("REPLACE", _kw("REPLACE"), "pulls in external files"),
        (
            "ACCEPT ... FROM ENVIRONMENT",
            _kw("ACCEPT") + r"[^.]*?" + _NB + r"FROM\s+ENVIRONMENT" + _NA,
            "reads the host environment",
        ),
        (
            "ACCEPT ... FROM COMMAND-LINE",
            _kw("ACCEPT") + r"[^.]*?" + _NB + r"FROM\s+COMMAND-LINE" + _NA,
            "reads host process arguments",
        ),
        (
            "DISPLAY ... UPON ENVIRONMENT",
            _kw("DISPLAY") + r"[^.]*?" + _NB + r"UPON\s+ENVIRONMENT" + _NA,
            "writes the host environment",
        ),
        (None, _NB + r"CBL_[A-Z0-9_-]*", "runtime library escape routine"),
        ("ENVIRONMENT-NAME", _kw("ENVIRONMENT-NAME"), "environment access clause"),
        ("ENVIRONMENT-VALUE", _kw("ENVIRONMENT-VALUE"), "environment access clause"),
        ("COMMAND-LINE", _kw("COMMAND-LINE"), "host process arguments"),
        # A format switch could make the scanner and cobc disagree on what is a comment.
        ("SOURCE FORMAT directive", r">>\s*SOURCE", "source format switch"),
    )
)

_CONT_RE = re.compile(r"-(?![\d(])")


def _clean_line(line: str) -> str:
    """Remove comments and blank string literal contents on one line.

    Only free-format comments are recognised (``*>`` anywhere outside a literal,
    or ``*`` / ``/`` in column 1), because programs are compiled with ``-free``.
    Literal contents are replaced so that text like "CALL" in a message is not
    an operator, while the quotes stay so tokens remain separated.
    """
    if line[:1] in ("*", "/"):
        return ""
    out: list[str] = []
    quote = ""
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if quote:
            if ch == quote:
                quote = ""
                out.append(ch)
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == "*" and line[i + 1 : i + 2] == ">":
            break
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _build(lines: list[str], join_continuations: bool) -> tuple[str, list[int]]:
    """Return (normalized text, per-character original line number)."""
    text: list[str] = []
    owner: list[int] = []

    def add(piece: str, lineno: int) -> None:
        text.append(piece)
        owner.extend([lineno] * len(piece))

    for lineno, raw in enumerate(lines, start=1):
        cleaned = _clean_line(raw).replace("\t", " ").strip()
        if not cleaned:
            continue
        if join_continuations and text and _CONT_RE.match(cleaned):
            # Fixed-format style continuation: resume the word directly.
            add(cleaned[1:].lstrip(), lineno)
        else:
            if text:
                add(" ", lineno)
            add(cleaned, lineno)
    return "".join(text), owner


def _collapse_ws(text: str, owner: list[int]) -> tuple[str, list[int]]:
    out: list[str] = []
    own: list[int] = []
    prev_space = False
    for ch, ln in zip(text, owner):
        if ch.isspace():
            if prev_space:
                continue
            ch = " "
            prev_space = True
        else:
            prev_space = False
        out.append(ch)
        own.append(ln)
    return "".join(out), own


def validate_source(source: str) -> ValidationResult:
    """Static scan. Never invokes a subprocess. Pure function of the source text."""
    if len(source.encode("utf-8", errors="replace")) > MAX_SOURCE_BYTES:
        return ValidationResult(
            False,
            ValidationError(
                "SOURCE-SIZE",
                0,
                f"Source exceeds the {MAX_SOURCE_BYTES // 1024} KB limit",
            ),
        )

    lines = re.split(r"\r\n|\r|\n", source)
    # Scan two views: continuation lines glued to the previous word, and plain
    # line joining. A prohibited token in either view is rejected.
    views = [_collapse_ws(*_build(lines, join)) for join in (True, False)]

    for label, pattern, why in _RULES:
        for text, owner in views:
            m = pattern.search(text)
            if m is None:
                continue
            token = label or m.group(0).upper()
            line = owner[m.start()]
            return ValidationResult(
                False,
                ValidationError(
                    token, line, f"Prohibited construct {token} at line {line}: {why}"
                ),
            )
    return ValidationResult(True, None)


# --------------------------------------------------------------------------
# CobolRunner
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CompileResult:
    success: bool
    binary_path: Path | None
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool = False


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    stdout: str  # possibly truncated, see ``truncated``
    stderr: str
    truncated: bool
    timed_out: bool
    duration_ms: float


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def _execute(
    args: list[str],
    *,
    cwd: Path,
    stdin_data: bytes,
    timeout: float,
    max_stdout: int,
    max_stderr: int,
) -> tuple[int, bytes, bytes, bool, bool, float]:
    """Run ``args`` (no shell). Returns code, stdout, stderr, truncated, timed_out, ms.

    Output is read incrementally and capped, so a program that floods STDOUT
    cannot exhaust memory. On timeout the whole process group is killed.
    """
    start = time.perf_counter()
    kwargs: dict = {}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        args,
        cwd=str(cwd),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        **kwargs,
    )
    buffers: dict[str, bytearray] = {"out": bytearray(), "err": bytearray()}
    truncated = {"out": False, "err": False}

    def drain(stream, key: str, limit: int) -> None:
        buf = buffers[key]
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                room = limit - len(buf)
                if room > 0:
                    buf.extend(chunk[:room])
                if len(chunk) > room:
                    truncated[key] = True
        except (OSError, ValueError):
            pass

    def feed() -> None:
        try:
            proc.stdin.write(stdin_data)
        except (OSError, ValueError):
            pass
        finally:
            try:
                proc.stdin.close()
            except (OSError, ValueError):
                pass

    threads = [
        threading.Thread(target=drain, args=(proc.stdout, "out", max_stdout), daemon=True),
        threading.Thread(target=drain, args=(proc.stderr, "err", max_stderr), daemon=True),
        threading.Thread(target=feed, daemon=True),
    ]
    for t in threads:
        t.start()

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        proc.wait()
    finally:
        if proc.poll() is None:
            _kill_tree(proc)
            proc.wait()
        for t in threads:
            t.join(timeout=2)
        for stream in (proc.stdout, proc.stderr):
            try:
                stream.close()
            except OSError:
                pass

    ms = (time.perf_counter() - start) * 1000.0
    return (
        proc.returncode,
        bytes(buffers["out"]),
        bytes(buffers["err"]),
        truncated["out"],
        timed_out,
        ms,
    )


def _text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


class CobolRunner:
    def __init__(self, cobc_path: str | None = None):
        self.cobc_path = cobc_path or os.environ.get("COBC_PATH") or "cobc"
        self._preset_lock = threading.Lock()
        self._preset_binaries: dict[str, Path] = {}
        self._preset_dir: Path | None = None
        self._preset_compile_results: dict[str, CompileResult] = {}

    # -- compile ---------------------------------------------------------
    def compile_source(self, source: str, sandbox_dir: Path) -> CompileResult:
        sandbox_dir = Path(sandbox_dir)
        src_path = sandbox_dir / "program.cbl"
        src_path.write_text(source, encoding="utf-8", newline="\n")
        args = [self.cobc_path, "-x", "-free", "-o", "program", "program.cbl"]
        start = time.perf_counter()
        try:
            code, out, err, _tr, timed_out, ms = _execute(
                args,
                cwd=sandbox_dir,
                stdin_data=b"",
                timeout=SUBPROCESS_TIMEOUT_S,
                max_stdout=MAX_STDERR_BYTES,
                max_stderr=MAX_STDERR_BYTES,
            )
        except FileNotFoundError:
            ms = (time.perf_counter() - start) * 1000.0
            return CompileResult(
                False, None, "", f"cobc executable not found: {self.cobc_path}", ms
            )
        binary = sandbox_dir / "program"
        if os.name == "nt" and not binary.exists():
            binary = sandbox_dir / "program.exe"
        ok = (not timed_out) and code == 0 and binary.exists()
        stderr = _text(err)
        if timed_out:
            stderr += "\ncobc timed out after 5 s"
        return CompileResult(ok, binary if ok else None, _text(out), stderr, ms, timed_out)

    # -- run -------------------------------------------------------------
    def run_batch(self, binary_path: Path, input_rows: list[str]) -> RunResult:
        binary_path = Path(binary_path)
        payload = ("\n".join(input_rows) + "\n").encode("utf-8") if input_rows else b""
        code, out, err, truncated, timed_out, ms = _execute(
            [str(binary_path)],
            cwd=binary_path.parent,
            stdin_data=payload,
            timeout=SUBPROCESS_TIMEOUT_S,
            max_stdout=MAX_STDOUT_BYTES,
            max_stderr=MAX_STDERR_BYTES,
        )
        return RunResult(code, _text(out), _text(err), truncated, timed_out, ms)

    # -- preset ----------------------------------------------------------
    def _preset_binary(self, preset_id: str) -> tuple[Path | None, CompileResult | None]:
        if preset_id not in PRESET_IDS:
            raise KeyError(f"Unknown preset_id: {preset_id}")
        with self._preset_lock:
            cached = self._preset_binaries.get(preset_id)
            if cached is not None and cached.exists():
                return cached, self._preset_compile_results.get(preset_id)
            if self._preset_dir is None:
                self._preset_dir = Path(tempfile.mkdtemp(prefix="vericobol_presets_"))
                atexit.register(shutil.rmtree, str(self._preset_dir), True)
            sandbox = self._preset_dir / preset_id
            sandbox.mkdir(exist_ok=True)
            source = (PRESETS_DIR / f"{preset_id}.cbl").read_text(encoding="utf-8")
            result = self.compile_source(source, sandbox)
            self._preset_compile_results[preset_id] = result
            if result.success and result.binary_path is not None:
                self._preset_binaries[preset_id] = result.binary_path
                return result.binary_path, result
            return None, result

    def compile_and_run_preset(self, preset_id: str, input_rows: list[str]) -> RunResult:
        binary, compiled = self._preset_binary(preset_id)
        if binary is None:
            detail = compiled.stderr if compiled else "preset compile failed"
            return RunResult(-1, "", detail, False, False, 0.0)
        return self.run_batch(binary, input_rows)

    # -- custom ----------------------------------------------------------
    def compile_and_run_custom(
        self, source: str, input_rows: list[str]
    ) -> tuple[CompileResult, RunResult | None]:
        verdict = validate_source(source)
        if not verdict.accepted:
            err = verdict.error
            msg = err.message if err else "Source rejected"
            return CompileResult(False, None, "", msg, 0.0), None
        tmp = tempfile.TemporaryDirectory(prefix="vericobol_")
        try:
            compiled = self.compile_source(source, Path(tmp.name))
            run: RunResult | None = None
            if compiled.success and compiled.binary_path is not None:
                run = self.run_batch(compiled.binary_path, input_rows)
            # binary_path points into a directory about to be deleted
            return (
                CompileResult(
                    compiled.success,
                    None,
                    compiled.stdout,
                    compiled.stderr,
                    compiled.duration_ms,
                    compiled.timed_out,
                ),
                run,
            )
        finally:
            tmp.cleanup()
