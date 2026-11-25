"""Stdlib AST scanner that rejects IEEE-754 float use in financial code.

Usage: python guard_no_float.py FILE [FILE ...]

Reports ``float(...)`` calls, float literals, ``float`` annotations and
``math.pow`` calls with line numbers. Exits 1 when any finding remains.

Allowed:
* functions whose name ends with ``_float_bug`` or starts with ``_fb_`` (the
  Float_Bug_Demo and its helpers) and the
  ``FLOAT_BUG_REGISTRY`` assignment;
* any finding on a line carrying a ``# float-ok`` pragma
  (e.g. ``start: float  # float-ok: timing``).
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

PRAGMA = "# float-ok"
DEMO_SUFFIX = "_float_bug"
DEMO_HELPER_PREFIX = "_fb_"  # helpers of the demo section in reference_models.py
DEMO_REGISTRY = "FLOAT_BUG_REGISTRY"


def _is_float_name(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Name) and node.id == "float"


def _annotation_has_float(node: ast.AST | None) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            node = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return False
    return any(_is_float_name(n) for n in ast.walk(node))


class _Scanner(ast.NodeVisitor):
    def __init__(self) -> None:
        self.findings: list[tuple[int, int, str]] = []  # (line, end_line, msg)

    def _add(self, node: ast.AST, msg: str) -> None:
        line = getattr(node, "lineno", 0)
        self.findings.append((line, getattr(node, "end_lineno", line) or line, msg))

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if node.name.endswith(DEMO_SUFFIX) or node.name.startswith(DEMO_HELPER_PREFIX):
            return
        args = node.args
        every = [*args.posonlyargs, *args.args, *args.kwonlyargs]
        every += [a for a in (args.vararg, args.kwarg) if a is not None]
        for a in every:
            if _annotation_has_float(a.annotation):
                self._add(a, f"float annotation on parameter '{a.arg}'")
        if _annotation_has_float(node.returns):
            self._add(node, f"float return annotation on '{node.name}'")
        self.generic_visit(node)

    visit_FunctionDef = _function
    visit_AsyncFunctionDef = _function

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if _annotation_has_float(node.annotation):
            self._add(node, "float annotation")
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id == DEMO_REGISTRY:
                return
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, float):
            self._add(node, f"float literal {node.value!r}")

    def visit_Call(self, node: ast.Call) -> None:
        f = node.func
        if _is_float_name(f):
            self._add(node, "float(...) call")
        elif (isinstance(f, ast.Attribute) and f.attr == "pow"
              and isinstance(f.value, ast.Name) and f.value.id == "math"):
            self._add(node, "math.pow call")
        self.generic_visit(node)


def scan_source(source: str) -> list[tuple[int, str]]:
    """Return sorted (line, message) findings for ``source``."""
    tree = ast.parse(source)
    lines = source.splitlines()
    scanner = _Scanner()
    scanner.visit(tree)
    out: list[tuple[int, str]] = []
    for line, end, msg in scanner.findings:
        span = lines[line - 1:max(end, line)]
        if any(PRAGMA in text for text in span):
            continue
        out.append((line, msg))
    return sorted(set(out))


def scan_file(path: Path) -> list[tuple[int, str]]:
    return scan_source(path.read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: guard_no_float.py FILE [FILE ...]", file=sys.stderr)
        return 2
    total = 0
    for arg in argv:
        path = Path(arg)
        try:
            findings = scan_file(path)
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            print(f"{path}: cannot scan: {exc}", file=sys.stderr)
            total += 1
            continue
        for line, msg in findings:
            print(f"{path}:{line}: {msg}")
        total += len(findings)
    if total:
        print(f"guard_no_float: {total} finding(s)", file=sys.stderr)
        return 1
    print("guard_no_float: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
