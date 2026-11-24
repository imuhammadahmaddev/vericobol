"""AST scan: the exact reference models must contain no float arithmetic.

Only the deliberately wrong ``*_float_bug`` functions (and their ``_fb_``
helpers in the marked float section) may use floats.

**Validates: Requirements 10.5, 19.8**
"""

from __future__ import annotations

import ast
from pathlib import Path

MODULE = Path(__file__).resolve().parent.parent / "app" / "reference_models.py"


def _is_float_section(name: str) -> bool:
    return name.endswith("_float_bug") or name.startswith("_fb_")


def _is_float_operand(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, float):
        return True
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "float"
    )


def _violations(node: ast.AST) -> list[str]:
    """Float constructs found anywhere under ``node``."""
    found: list[str] = []
    for n in ast.walk(node):
        line = getattr(n, "lineno", "?")
        if isinstance(n, ast.Constant) and isinstance(n.value, float):
            found.append(f"line {line}: float literal {n.value!r}")
        elif isinstance(n, ast.Name) and n.id == "float":
            found.append(f"line {line}: use of float (call or annotation)")
        elif isinstance(n, ast.Attribute) and n.attr == "pow":
            found.append(f"line {line}: .pow attribute (math.pow?)")
        elif isinstance(n, ast.Name) and n.id == "pow":
            found.append(f"line {line}: pow()")
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in n.names]
            if isinstance(n, ast.ImportFrom):
                mods.append(n.module or "")
            if any(m.split(".")[0] == "math" for m in mods):
                found.append(f"line {line}: math import")
        elif isinstance(n, ast.BinOp) and isinstance(n.op, ast.Pow):
            if _is_float_operand(n.left) or _is_float_operand(n.right):
                found.append(f"line {line}: ** with float operand")
        elif isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
            # True division on ints yields a float; the exact models use //.
            found.append(f"line {line}: true division '/' (yields float)")
    return found


def _split_top_level() -> tuple[list[ast.stmt], list[ast.stmt]]:
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    exact: list[ast.stmt] = []
    bug: list[ast.stmt] = []
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_float_section(stmt.name):
            bug.append(stmt)
        else:
            exact.append(stmt)
    return exact, bug


def test_exact_models_contain_no_float_constructs():
    exact, _ = _split_top_level()
    problems = [v for stmt in exact for v in _violations(stmt)]
    assert not problems, "float usage outside the float-bug section:\n" + "\n".join(problems)


def test_float_bug_section_is_detected_by_scan():
    """Sanity check: the scanner really finds floats where they exist."""
    _, bug = _split_top_level()
    names = {s.name for s in bug}
    assert {"loan_calc_float_bug", "compound_int_float_bug", "daily_ledger_float_bug"} <= names

    for stmt in bug:
        if stmt.name.endswith("_float_bug"):
            assert _violations(stmt), f"scan found no float in {stmt.name}"

    kinds = " ".join(v for s in bug for v in _violations(s))
    assert "float literal" in kinds
    assert "use of float" in kinds
    assert "** with float operand" in kinds or "true division" in kinds


def test_scanner_flags_synthetic_violations():
    for src in ("x = 1.5", "x = float(a)", "x = math.pow(a, b)", "x = a ** 0.5", "x = a / b"):
        assert _violations(ast.parse(src)), src
    assert not _violations(ast.parse("x = Decimal('1.5') ** 2 // 3"))
