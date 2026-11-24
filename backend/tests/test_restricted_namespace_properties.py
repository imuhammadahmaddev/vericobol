"""Property 11: Restricted namespace blocks every forbidden construct.

# Feature: cobol-verification-engine, Property 11: Restricted namespace blocks every forbidden construct

**Validates: Requirements 12.3**

Each generated snippet embeds exactly one forbidden operation (import,
from-import, open, eval, exec, __import__, compile, dunder access, getattr and
other introspection escapes) in otherwise valid code. The payload, if it ever
ran, would create a canary path inside a temp directory. The tests assert the
restricted loader rejects the code and that the canary was never created.
"""
from __future__ import annotations

import tempfile
from decimal import Decimal
from pathlib import Path

from hypothesis import given, settings, strategies as st

from app.pbt_verifier import (
    Case,
    RestrictedCodeError,
    load_restricted_function,
    run_user_python,
)

# {P} is replaced by a repr() of the canary path. Each template is one
# forbidden operation that would create the canary if it were executed.
_FORBIDDEN = [
    "import os\nos.mkdir({P})",
    "import os as _o\n_o.mkdir({P})",
    "import pathlib\npathlib.Path({P}).mkdir()",
    "from os import mkdir\nmkdir({P})",
    "from pathlib import Path as _P\n_P({P}).mkdir()",
    "open({P}, 'w').write('x')",
    "_f = open({P}, 'w')",
    "eval(\"open({P}, 'w')\")",
    "exec(\"open({P}, 'w')\")",
    "__import__('os').mkdir({P})",
    "_m = __import__('os')\n_m.mkdir({P})",
    "compile(\"open({P}, 'w')\", 'x', 'exec')",
    "_c = (1).__class__",
    "_c = Decimal.__dict__",
    "_c = Decimal.__init__.__globals__",
    "_c = ().__class__.__bases__[0].__subclasses__()",
    "_c = (lambda: 0).__globals__",
    "_c = (lambda: 0).__code__",
    "_c = getattr(Decimal, 'ln')",
    "setattr(Decimal, 'x', 1)",
    "_c = globals()",
    "_c = locals()",
    "_c = vars(Decimal)",
    "_c = type(1)",
    "_c = (lambda: 0).func_globals",
    "_c = Decimal.mro()",
    "_c = '{{0.__class__}}'.format(1)",
    "_c = (i for i in ()).gi_frame",
    "_c = object",
    "_c = super",
    "breakpoint()",
    "input()",
]

_INDENTS = ["  ", "    ", "\t", "        "]
_PLACEMENTS = ["module_top", "module_bottom", "func_start", "func_mid",
               "func_end", "nested_if", "nested_for"]

_VALID_BODY = [
    "x = principal * Decimal('2')",
    "y = (principal + Decimal('0.01')).quantize(Decimal('0.01'), rounding=ROUND_DOWN)",
    "z = max(Decimal(0), principal) / Decimal(3)",
    "# a comment",
    "",
    "w = abs(principal) + Decimal('1.005')",
    "n = int(principal) + len('abc') + sum([1, 2, 3])",
]


def _pad(line: str, indent: str, extra_spaces: int) -> str:
    # Random extra whitespace inside the statement text itself (outside strings
    # is not guaranteed, so only add trailing spaces and a trailing comment).
    return f"{indent}{line}{' ' * extra_spaces}"


def _build(template: str, placement: str, indent: str, canary: Path,
           pre: list[str], post: list[str], trail: int, blank: int) -> str:
    payload_lines = template.replace("{P}", repr(str(canary))).split("\n")

    def block(level: str) -> list[str]:
        return [_pad(ln, level, trail) for ln in payload_lines]

    def valid(level: str, lines: list[str]) -> list[str]:
        return [f"{level}{ln}" if ln else "" for ln in lines]

    body_pre = valid(indent, pre)
    body_post = valid(indent, post)
    head = ["def calculate(principal):"]
    tail = [f"{indent}return {{'result': principal}}"]
    gap = [""] * blank
    if placement == "module_top":
        return "\n".join(block("") + gap + head + body_pre + body_post + tail) + "\n"
    if placement == "module_bottom":
        return "\n".join(head + body_pre + body_post + tail + gap + block("")) + "\n"
    if placement == "func_start":
        mid = block(indent) + body_pre + body_post
    elif placement == "func_mid":
        mid = body_pre + gap + block(indent) + body_post
    elif placement == "func_end":
        mid = body_pre + body_post + block(indent)
    elif placement == "nested_if":
        mid = body_pre + [f"{indent}if principal >= 0:"] + block(indent * 2) + body_post
    else:  # nested_for
        mid = body_pre + [f"{indent}for _i in range(1):"] + block(indent * 2) + body_post
    return "\n".join(head + mid + tail) + "\n"


@st.composite
def _snippets(draw):
    return (
        draw(st.sampled_from(_FORBIDDEN)),
        draw(st.sampled_from(_PLACEMENTS)),
        draw(st.sampled_from(_INDENTS)),
        draw(st.lists(st.sampled_from(_VALID_BODY), max_size=4)),
        draw(st.lists(st.sampled_from(_VALID_BODY), max_size=3)),
        draw(st.integers(0, 3)),
        draw(st.integers(0, 2)),
    )


# Feature: cobol-verification-engine, Property 11: Restricted namespace blocks every forbidden construct
@settings(max_examples=200, database=None, deadline=None)
@given(_snippets())
def test_property_11_loader_rejects_forbidden_constructs(snippet):
    template, placement, indent, pre, post, trail, blank = snippet
    with tempfile.TemporaryDirectory() as tmp:
        canary = Path(tmp) / "canary"
        code = _build(template, placement, indent, canary, pre, post, trail, blank)
        try:
            load_restricted_function(code)
        except RestrictedCodeError:
            pass
        else:
            raise AssertionError(f"forbidden code was accepted:\n{code}")
        assert not canary.exists(), f"payload executed:\n{code}"
        assert list(Path(tmp).iterdir()) == []


# Feature: cobol-verification-engine, Property 11: Restricted namespace blocks every forbidden construct
@settings(max_examples=100, database=None, deadline=None)
@given(_snippets())
def test_property_11_run_user_python_never_executes_payload(snippet):
    template, placement, indent, pre, post, trail, blank = snippet
    with tempfile.TemporaryDirectory() as tmp:
        canary = Path(tmp) / "canary"
        code = _build(template, placement, indent, canary, pre, post, trail, blank)
        cases = [Case("x", {"principal": Decimal("1.00")})]
        results = run_user_python(code, cases, timeout_s=5.0)
        assert len(results) == 1
        assert results[0].outputs is None, f"forbidden code produced output:\n{code}"
        assert results[0].error, f"no error reported:\n{code}"
        assert not canary.exists(), f"payload executed:\n{code}"


# Feature: cobol-verification-engine, Property 11: Restricted namespace blocks every forbidden construct
@settings(max_examples=100, database=None, deadline=None)
@given(
    pre=st.lists(st.sampled_from(_VALID_BODY), max_size=5),
    indent=st.sampled_from(_INDENTS),
    mult=st.integers(min_value=0, max_value=10_000),
)
def test_property_11_valid_decimal_code_is_accepted_and_runs(pre, indent, mult):
    # Control: the same scaffolding without a forbidden construct loads and runs.
    body = [f"{indent}{ln}" if ln else "" for ln in pre]
    code = "\n".join(
        ["def calculate(principal):"] + body
        + [f"{indent}return {{'result': (principal * Decimal({mult})).quantize("
           f"Decimal('0.01'), rounding=ROUND_HALF_UP)}}"]) + "\n"
    fn = load_restricted_function(code)
    out = fn(principal=Decimal("1.005"))
    assert out == {"result": (Decimal("1.005") * mult).quantize(Decimal("0.01"), "ROUND_HALF_UP")}


def test_property_11_valid_code_runs_in_child_process():
    code = (
        "def calculate(principal):\n"
        "    return {'result': (principal * Decimal('2')).quantize(Decimal('0.01'),"
        " rounding=ROUND_DOWN)}\n"
    )
    cases = [Case("x", {"principal": Decimal("1.234")}),
             Case("x", {"principal": Decimal("0.00")})]
    results = run_user_python(code, cases)
    assert [r.error for r in results] == [None, None]
    assert results[0].outputs == {"result": Decimal("2.46")}
    assert results[1].outputs == {"result": Decimal("0.00")}
