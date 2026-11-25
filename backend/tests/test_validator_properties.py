"""Property test for the static Security_Validator (validate_source).

Pure text analysis: no cobc and no subprocess are needed.
"""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from app.cobol_runner import validate_source

# (words of the construct, accepted token labels). The first word may be split
# mid-word across a "-" continuation line unless ``no_split`` is set.
_CONSTRUCTS: tuple[tuple[tuple[str, ...], tuple[str, ...], bool], ...] = (
    (("CALL", "WS-PROG"), ("CALL",), False),
    (("MOVE", "1", "TO", "SYSTEM"), ("SYSTEM",), False),
    (("OPEN", "OUTPUT", "OUT-FILE"), ("OPEN",), False),
    (("OPEN", "EXTEND", "OUT-FILE"), ("OPEN",), False),
    (("OPEN", "I-O", "OUT-FILE"), ("OPEN",), False),
    (("SELECT", "OUT-FILE", "ASSIGN", "TO", "WS-NAME"), ("SELECT ... ASSIGN",), False),
    (("READ", "IN-FILE"), ("READ",), False),
    (("WRITE", "OUT-REC"), ("WRITE",), False),
    (("REWRITE", "OUT-REC"), ("REWRITE", "WRITE"), True),
    (("DELETE", "IN-FILE"), ("DELETE",), False),
    (("COPY", "BOOK"), ("COPY",), False),
    (("REPLACE", "==A==", "BY", "==B=="), ("REPLACE",), False),
    (("ACCEPT", "A", "FROM", "ENVIRONMENT", "WS-NAME"), ("ACCEPT ... FROM ENVIRONMENT",), False),
    (("ACCEPT", "A", "FROM", "COMMAND-LINE"), ("ACCEPT ... FROM COMMAND-LINE",), False),
    (("DISPLAY", "A", "UPON", "ENVIRONMENT"), ("DISPLAY ... UPON ENVIRONMENT",), False),
    (("MOVE", "1", "TO", "CBL_FOO_BAR"), ("CBL_FOO_BAR",), False),
)

_ALLOWED_STATEMENTS = (
    ("ACCEPT", "A"),
    ("ACCEPT", "A", "FROM", "SYSIN"),
    ("DISPLAY", "A"),
    ("DISPLAY", "A", "UPON", "SYSOUT"),
    ("DISPLAY", "A", "UPON", "SYSOUT", "WITH", "NO", "ADVANCING"),
)

_HEAD = [
    "IDENTIFICATION DIVISION.",
    "PROGRAM-ID. T.",
    "{ENV}",
    "DATA DIVISION.",
    "WORKING-STORAGE SECTION.",
    "01 A PIC 9(3).",
    "PROCEDURE DIVISION.",
]
_TAIL = ["    STOP RUN."]


@st.composite
def _cased(draw, word: str) -> str:
    flips = draw(st.lists(st.booleans(), min_size=len(word), max_size=len(word)))
    return "".join(c.lower() if f else c for c, f in zip(word, flips))


@st.composite
def _gap(draw) -> str:
    return " " * draw(st.integers(1, 4))


@st.composite
def _comments(draw) -> list[str]:
    n = draw(st.integers(0, 3))
    texts = ["*> note", "*> CALL is only a comment", "  *> READ in a comment"]
    return [draw(st.sampled_from(texts)) for _ in range(n)]


@st.composite
def _env_header(draw) -> str:
    a = draw(_cased("ENVIRONMENT"))
    b = draw(_cased("DIVISION"))
    return f"{a}{draw(_gap())}{b}."


@st.composite
def _statement_lines(draw, words: tuple[str, ...], allow_split: bool):
    """Render ``words`` as one or more source lines (first line starts with a space)."""
    cased = [draw(_cased(w)) if w.isalpha() or "-" in w else w for w in words]
    cased = [c if i == 0 or not words[i].isalpha() else c for i, c in enumerate(cased)]
    indent = " " * draw(st.integers(1, 8))
    lines: list[str] = []
    word_line = [0]
    first = cased[0]
    rest = cased[1:]
    if allow_split and len(first) > 2 and draw(st.booleans()):
        cut = draw(st.integers(1, len(first) - 1))
        lines.append(indent + first[:cut])
        current = "-" + first[cut:]
    else:
        current = first
        current = indent + current
    for w in rest:
        if draw(st.booleans()):
            lines.append(current)
            current = " " * draw(st.integers(1, 4)) + w
        else:
            current += draw(_gap()) + w
        word_line.append(len(lines))
    lines.append(current + draw(st.sampled_from(["", " .", "."])))
    return lines, word_line


@settings(max_examples=150, database=None, deadline=None)
@given(data=st.data(), idx=st.integers(0, len(_CONSTRUCTS) - 1))
def test_prohibited_constructs_rejected(data, idx):
    # Feature: cobol-verification-engine, Property 9: Security validator rejects every prohibited construct regardless of case or line-splitting
    # Validates: Requirements 8.1, 8.2, 8.3, 8.4, 8.5, 8.9, 8.10, 8.11, 8.12
    words, tokens, no_split = _CONSTRUCTS[idx]
    # The reported line is that of the first word of the offending token.
    offender = {"SYSTEM": 3, "CBL_FOO_BAR": 3}.get(tokens[0], 0)
    env = data.draw(_env_header())
    lines = [h.replace("{ENV}", env) for h in _HEAD]
    lines += data.draw(_comments())
    start_line = len(lines) + 1
    stmt, word_line = data.draw(_statement_lines(words, not no_split))
    lines += stmt
    start_line += word_line[offender]
    lines += _TAIL
    nl = data.draw(st.sampled_from(["\n", "\r\n"]))
    source = nl.join(lines) + nl

    result = validate_source(source)

    assert not result.accepted, source
    err = result.error
    assert err is not None
    assert err.token.upper() in tokens, (err, source)
    assert err.line == start_line, (err, start_line, source)
    assert err.token in err.message and str(err.line) in err.message


@settings(max_examples=100, database=None, deadline=None)
@given(data=st.data())
def test_allowed_forms_accepted(data):
    # Feature: cobol-verification-engine, Property 9: Security validator rejects every prohibited construct regardless of case or line-splitting (allowed forms accepted)
    # Validates: Requirements 8.1, 8.2, 8.3, 8.4, 8.5, 8.9, 8.10, 8.11, 8.12
    env = data.draw(_env_header())
    lines = [h.replace("{ENV}", env) for h in _HEAD]
    stmts = data.draw(st.lists(st.sampled_from(_ALLOWED_STATEMENTS), min_size=1, max_size=4))
    for words in stmts:
        lines += data.draw(_comments())
        lines += data.draw(_statement_lines(words, False))[0]
    lines += _TAIL
    source = "\n".join(lines) + "\n"

    result = validate_source(source)

    assert result.accepted, (result.error, source)
    assert result.error is None
