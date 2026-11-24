"""Unit tests for validate_source and CobolRunner (task 5.4).

Run inside Docker (needs cobc and a POSIX shell).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app import cobol_runner
from app.cobol_runner import (
    MAX_SOURCE_BYTES,
    MAX_STDOUT_BYTES,
    PRESETS_DIR,
    CobolRunner,
    validate_source,
)

HEADER = (
    "IDENTIFICATION DIVISION.\n"
    "PROGRAM-ID. T.\n"
    "PROCEDURE DIVISION.\n"
)
GOOD = HEADER + "    DISPLAY 'OK'.\n    STOP RUN.\n"
INFINITE = HEADER + "    PERFORM UNTIL 1 = 2\n        CONTINUE\n    END-PERFORM.\n    STOP RUN.\n"


def _prog(stmt: str) -> str:
    # statement lands on line 4
    return HEADER + f"    {stmt}\n    STOP RUN.\n"


# ---------------------------------------------------------------- validator
@pytest.mark.parametrize(
    "stmt,token",
    [
        ('CALL "SYSTEM" USING CMD.', "CALL"),
        ('CALL "other" USING X.', "CALL"),
        ("OPEN OUTPUT F.", "OPEN OUTPUT/EXTEND/I-O"),
        ("OPEN EXTEND F.", "OPEN OUTPUT/EXTEND/I-O"),
        ("OPEN I-O F.", "OPEN OUTPUT/EXTEND/I-O"),
        ("SELECT F ASSIGN TO 'x'.", "SELECT ... ASSIGN"),
        ("READ F.", "READ"),
        ("WRITE R.", "WRITE"),
        ("REWRITE R.", "REWRITE"),
        ("DELETE F.", "DELETE"),
        ("COPY LIB.", "COPY"),
        ("REPLACE ==A== BY ==B==.", "REPLACE"),
        ("ACCEPT X FROM ENVIRONMENT 'HOME'.", "ACCEPT ... FROM ENVIRONMENT"),
        ("DISPLAY 'A' UPON ENVIRONMENT-NAME.", "ENVIRONMENT-NAME"),
        ("DISPLAY 'A' UPON ENVIRONMENT-VALUE.", "ENVIRONMENT-VALUE"),
        ("ACCEPT X FROM ENVIRONMENT-VALUE.", "ENVIRONMENT-VALUE"),
        ("ACCEPT X FROM COMMAND-LINE.", "ACCEPT ... FROM COMMAND-LINE"),
        ("DISPLAY 'A' UPON ENVIRONMENT.", "DISPLAY ... UPON ENVIRONMENT"),
        ("CBL_OC_DUMP.", "CBL_OC_DUMP"),
    ],
)
def test_rejection_names_token_and_line(stmt, token):
    res = validate_source(_prog(stmt))
    assert not res.accepted
    assert res.error.token == token
    assert res.error.line == 4
    assert token in res.error.message and "4" in res.error.message


def test_rejection_is_case_insensitive_and_multiline():
    res = validate_source(HEADER + "    open\n      output F.\n    STOP RUN.\n")
    assert not res.accepted
    assert res.error.token == "OPEN OUTPUT/EXTEND/I-O"
    assert res.error.line == 4


def test_tokens_in_comments_literals_and_identifiers_ignored():
    src = (
        HEADER
        + "    *> CALL 'SYSTEM' in a comment\n"
        + "    DISPLAY 'CALL SYSTEM READ'.\n"
        + "    MOVE 1 TO WS-READ-COUNT.\n"
        + "    STOP RUN.\n"
    )
    assert validate_source(src).accepted


@pytest.mark.parametrize("preset", ["loan_calc", "compound_int", "daily_ledger"])
def test_presets_accepted(preset):
    src = (PRESETS_DIR / f"{preset}.cbl").read_text(encoding="utf-8")
    res = validate_source(src)
    assert res.accepted and res.error is None


def test_environment_division_accepted():
    src = (
        "IDENTIFICATION DIVISION.\nPROGRAM-ID. T.\n"
        "ENVIRONMENT DIVISION.\nCONFIGURATION SECTION.\n"
        "PROCEDURE DIVISION.\n    STOP RUN.\n"
    )
    assert validate_source(src).accepted


def test_size_boundary():
    at = "*> " + "x" * (MAX_SOURCE_BYTES - len(GOOD.encode()) - 4) + "\n"
    ok = GOOD + at
    assert len(ok.encode()) == MAX_SOURCE_BYTES
    assert validate_source(ok).accepted
    over = ok + "x"
    assert len(over.encode()) == MAX_SOURCE_BYTES + 1
    res = validate_source(over)
    assert not res.accepted and res.error.token == "SOURCE-SIZE"


# ------------------------------------------------------------ rejected path
def test_rejected_source_never_invokes_cobc(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("subprocess invoked for rejected source")

    monkeypatch.setattr(cobol_runner.subprocess, "Popen", boom)
    monkeypatch.setattr(cobol_runner.subprocess, "run", boom)
    comp, run = CobolRunner().compile_and_run_custom(_prog('CALL "SYSTEM".'), ["1"])
    assert not comp.success and run is None
    assert "CALL" in comp.stderr and "4" in comp.stderr


# ------------------------------------------------------------ sandbox cleanup
@pytest.fixture
def sandboxes(monkeypatch):
    seen: list[Path] = []
    orig = CobolRunner.compile_source

    def spy(self, source, sandbox_dir):
        seen.append(Path(sandbox_dir))
        assert Path(sandbox_dir).is_dir()
        return orig(self, source, sandbox_dir)

    monkeypatch.setattr(CobolRunner, "compile_source", spy)
    return seen


def test_cleanup_after_success(sandboxes):
    comp, run = CobolRunner().compile_and_run_custom(GOOD, [])
    assert comp.success and run is not None and run.exit_code == 0
    assert "OK" in run.stdout
    assert len(sandboxes) == 1 and not sandboxes[0].exists()


def test_cleanup_after_compile_failure(sandboxes):
    comp, run = CobolRunner().compile_and_run_custom("THIS IS NOT COBOL\n", [])
    assert not comp.success and run is None
    assert len(sandboxes) == 1 and not sandboxes[0].exists()


def test_cleanup_after_run_failure(sandboxes, monkeypatch):
    orig = CobolRunner.run_batch

    def failing(self, binary, rows):
        raise RuntimeError("run exploded")

    monkeypatch.setattr(CobolRunner, "run_batch", failing)
    with pytest.raises(RuntimeError):
        CobolRunner().compile_and_run_custom(GOOD, [])
    assert len(sandboxes) == 1 and not sandboxes[0].exists()
    monkeypatch.setattr(CobolRunner, "run_batch", orig)


def test_cleanup_after_nonzero_exit(sandboxes):
    src = HEADER + "    MOVE 3 TO RETURN-CODE.\n    STOP RUN.\n"
    comp, run = CobolRunner().compile_and_run_custom(src, [])
    assert comp.success and run.exit_code == 3
    assert not sandboxes[0].exists()


def test_cleanup_after_timeout(sandboxes, monkeypatch):
    monkeypatch.setattr(cobol_runner, "SUBPROCESS_TIMEOUT_S", 1.0)
    # compile must be allowed to finish: only the run is expected to time out
    comp, run = CobolRunner().compile_and_run_custom(INFINITE, [])
    assert comp.success, comp.stderr
    assert run.timed_out
    assert not sandboxes[0].exists()


# ------------------------------------------------------- subprocess arguments
def test_popen_receives_list_args_without_shell(monkeypatch):
    calls = []
    real = subprocess.Popen

    def spy(args, *a, **k):
        calls.append((args, k))
        return real(args, *a, **k)

    monkeypatch.setattr(cobol_runner.subprocess, "Popen", spy)
    comp, run = CobolRunner().compile_and_run_custom(GOOD, ["1"])
    assert comp.success and run.exit_code == 0
    assert len(calls) == 2  # one compile, one run
    for args, kw in calls:
        assert isinstance(args, list) and all(isinstance(x, str) for x in args)
        assert kw.get("shell", False) is False
    assert calls[0][0][0] == "cobc" or calls[0][0][0].endswith("cobc")
    assert "-x" in calls[0][0]


def test_cobc_path_from_runner_argument(monkeypatch):
    calls = []

    def fake(args, *a, **k):
        calls.append(args)
        raise FileNotFoundError

    monkeypatch.setattr(cobol_runner.subprocess, "Popen", fake)
    comp, _ = CobolRunner(cobc_path="/nope/cobc").compile_and_run_custom(GOOD, [])
    assert calls[0][0] == "/nope/cobc"
    assert not comp.success and "not found" in comp.stderr


# ------------------------------------------------- run_batch with script binary
def _script(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "prog.sh"
    p.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8", newline="\n")
    p.chmod(0o755)
    return p


def test_stdout_truncated_at_1mb(tmp_path):
    p = _script(tmp_path, "head -c 3000000 /dev/zero | tr '\\0' a")
    run = CobolRunner().run_batch(p, [])
    assert run.truncated
    assert len(run.stdout.encode()) == MAX_STDOUT_BYTES


def test_stdout_under_limit_not_truncated(tmp_path):
    p = _script(tmp_path, "echo hello")
    run = CobolRunner().run_batch(p, [])
    assert not run.truncated and run.stdout == "hello\n"


def test_empty_stdin_gives_zero_bytes(tmp_path):
    p = _script(tmp_path, "cat | wc -c")
    run = CobolRunner().run_batch(p, [])
    assert run.exit_code == 0 and run.stdout.strip() == "0"


def test_rows_are_newline_joined_on_stdin(tmp_path):
    p = _script(tmp_path, "cat")
    run = CobolRunner().run_batch(p, ["a", "b"])
    assert run.stdout == "a\nb\n"


def test_run_timeout_kills_process(tmp_path, monkeypatch):
    monkeypatch.setattr(cobol_runner, "SUBPROCESS_TIMEOUT_S", 0.5)
    p = _script(tmp_path, "sleep 30")
    run = CobolRunner().run_batch(p, [])
    assert run.timed_out
    assert run.duration_ms < 5000
