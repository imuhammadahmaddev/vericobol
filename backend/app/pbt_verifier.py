"""PBT_Verifier: adversarial Case generation (task 7.1).

Later tasks (7.2, 7.3) add comparison, reporting and ``run_verification``
to this module in the sections marked below.

Money values are never produced via ``float``: every amount is drawn as an
integer count of the field's smallest unit (cents, or thousandths for rates)
and converted with ``Decimal.scaleb``, which is exact.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from hypothesis import HealthCheck, Phase, seed as hyp_seed, settings, strategies as st
from hypothesis import given

# ---------------------------------------------------------------------------
# Section 1: data types
# ---------------------------------------------------------------------------

MIN_CASES = 150
MAX_CASES = 250

_CATALOG_PATH = Path(__file__).resolve().parent.parent / "cobol_samples" / "presets.json"


@dataclass(frozen=True)
class Case:
    preset_id: str
    inputs: dict[str, Decimal | int]


# ---------------------------------------------------------------------------
# Section 2: catalog access
# ---------------------------------------------------------------------------

_CATALOG_CACHE: dict | None = None


def _catalog() -> dict:
    global _CATALOG_CACHE
    if _CATALOG_CACHE is None:
        with _CATALOG_PATH.open("r", encoding="utf-8") as fh:
            _CATALOG_CACHE = json.load(fh, parse_float=Decimal)
    return _CATALOG_CACHE


@dataclass(frozen=True)
class _Field:
    name: str
    scale: int          # decimal places (0 for integers)
    lo: int             # minimum, in units of 10**-scale
    hi: int             # maximum, in units of 10**-scale


def _units(text: str, scale: int) -> int:
    return int(Decimal(text).scaleb(scale))


def _fields(preset_id: str) -> list[_Field]:
    try:
        schema = _catalog()[preset_id]["input_schema"]
    except KeyError:
        raise ValueError(f"unknown preset_id: {preset_id!r}") from None
    out = []
    for f in schema:
        scale = max(0, -Decimal(f["step"]).as_tuple().exponent)
        # Rate step is 0.001 but min/max carry the declared PIC scale too.
        scale = max(scale, -Decimal(f["min"]).as_tuple().exponent,
                    -Decimal(f["max"]).as_tuple().exponent, 0)
        out.append(_Field(f["name"], scale, _units(f["min"], scale), _units(f["max"], scale)))
    return out


def _to_value(units: int, scale: int) -> Decimal | int:
    """Exact conversion from integer units to Decimal (int when scale is 0)."""
    if scale == 0:
        return units
    return Decimal(units).scaleb(-scale)


def _build(preset_id: str, fields: list[_Field], units: dict[str, int]) -> Case:
    inputs = {f.name: _to_value(units[f.name], f.scale) for f in fields}
    return Case(preset_id, inputs)


def _key(case: Case) -> tuple:
    return tuple((k, str(v)) for k, v in case.inputs.items())


# ---------------------------------------------------------------------------
# Section 3: deterministic boundary cases
# ---------------------------------------------------------------------------

def _loan_edges(fs: dict[str, _Field]) -> list[dict[str, int]]:
    p, r, t = fs["principal"], fs["annual_rate_pct"], fs["term_months"]
    # 0.00 is outside the loan_calc catalog range (min 0.01); 0.01 is used instead.
    principals = [p.lo, 1_00, 999_99, 1_000_00, 250_000_00, p.hi - 1, p.hi]
    # zero, 0.001, half-cent style x.xx5, repeating 3.333, typical, near-max, max
    rates = [r.lo, 1, 5, 2_125, 3_333, 6_500, 99_999, r.hi]
    terms = [t.lo, 2, 12, 359, t.hi]
    out = []
    for i, rate in enumerate(rates):
        for j, term in enumerate(terms):
            out.append({"principal": principals[(i + j) % len(principals)],
                        "annual_rate_pct": rate, "term_months": term})
    for pr, rate, term in [
        (p.hi, r.hi, t.hi), (p.hi, r.lo, t.lo), (p.hi, 3_333, t.hi), (p.hi, 3_333, t.lo),
        (p.lo, r.lo, t.lo), (p.lo, 3_333, t.lo), (p.lo, r.hi, t.hi), (p.hi - 1, 5, t.hi),
        (1_000_00, 3_333, t.lo), (1_000_00, 3_333, 3),
    ]:
        out.append({"principal": pr, "annual_rate_pct": rate, "term_months": term})
    return out


def _compound_edges(fs: dict[str, _Field]) -> list[dict[str, int]]:
    p, r, c, y = (fs["principal"], fs["annual_rate_pct"],
                  fs["compounds_per_year"], fs["years"])
    principals = [p.lo, 1, 1_00, 12_345_67, 10_000_00, p.hi - 1, p.hi]
    rates = [r.lo, 1, 5, 2_125, 3_333, 5_000, 19_995, r.hi]
    freq_years = [(c.lo, y.lo), (c.hi, y.hi), (c.hi, y.lo), (c.lo, y.hi), (4, 10), (12, 10)]
    out = []
    for i, rate in enumerate(rates):
        for j, (cpy, yrs) in enumerate(freq_years):
            out.append({"principal": principals[(i + j) % len(principals)],
                        "annual_rate_pct": rate, "compounds_per_year": cpy, "years": yrs})
    for pr, rate, cpy, yrs in [
        (p.hi, r.hi, c.hi, y.hi), (p.hi, r.lo, c.hi, y.hi), (p.hi, 3_333, c.hi, y.hi),
        (p.lo, 3_333, c.lo, y.lo), (p.lo, r.hi, c.hi, y.hi), (1, 3_333, 12, 1),
        (1_000_00, 3_333, 12, 1), (p.hi, 5, 12, 1),
    ]:
        out.append({"principal": pr, "annual_rate_pct": rate,
                    "compounds_per_year": cpy, "years": yrs})
    return out


def _ledger_from_net(fs: dict[str, _Field], net: int, threshold: int, fee: int,
                     pad: int) -> dict[str, int] | None:
    """Build inputs with opening + deposits - withdrawals == net (in cents).

    ``pad`` is added to both deposits and withdrawals so the net is unchanged.
    Returns None when the net cannot be represented within the catalog ranges.
    """
    o, d, w = fs["opening_balance"], fs["deposits"], fs["withdrawals"]
    opening = min(max(net, o.lo), o.hi)
    diff = net - opening
    dep, wd = (diff, 0) if diff >= 0 else (0, -diff)
    dep += pad
    wd += pad
    if not (d.lo <= dep <= d.hi and w.lo <= wd <= w.hi):
        return None
    return {"opening_balance": opening, "deposits": dep, "withdrawals": wd,
            "overdraft_threshold": threshold, "overdraft_fee": fee}


def _ledger_edges(fs: dict[str, _Field]) -> list[dict[str, int]]:
    th = fs["overdraft_threshold"]
    fee_f = fs["overdraft_fee"]
    fees = [0, 1, 35_00, 25_50, fee_f.hi]
    out: list[dict[str, int]] = []
    n = 0

    def add(net: int, threshold: int, pad: int) -> None:
        nonlocal n
        row = _ledger_from_net(fs, net, threshold, fees[n % len(fees)], pad)
        if row is not None:
            out.append(row)
            n += 1

    # Net balance 0.00 / -0.01 with the default threshold 0.00 (Req 11.4).
    for net in (0, -1, 1):
        for pad in (0, 1_234_56, 50_000_000_00):
            add(net, 0, pad)
    # Threshold-relative: positive, negative, zero (and extremes) x {=, -0.01, +0.01}.
    thresholds = [0, 100_00, 5_000_50, 1, -100_00, -5_000_50, -1, th.hi, th.lo]
    for threshold in thresholds:
        for delta in (0, -1, 1):
            for pad in (0, 1_234_56):
                add(threshold + delta, threshold, pad)
    # Zero everything, and near-max balances.
    out.append({"opening_balance": 0, "deposits": 0, "withdrawals": 0,
                "overdraft_threshold": 0, "overdraft_fee": 0})
    out.append({"opening_balance": 0, "deposits": 0, "withdrawals": 0,
                "overdraft_threshold": 0, "overdraft_fee": 1})
    ob, db, wb = fs["opening_balance"], fs["deposits"], fs["withdrawals"]
    for opening, dep, wd, threshold in [
        (ob.hi, db.hi, 0, 0), (ob.hi, db.hi, wb.hi, 0), (ob.hi - 1, db.hi, 0, th.hi),
        (ob.lo, 0, wb.hi, 0), (ob.lo, 0, wb.hi, th.lo), (ob.lo, 0, 1, th.lo),
        (ob.lo + 1, 0, 0, th.lo), (0, db.hi, wb.hi, 0), (ob.hi, 0, wb.hi, th.hi),
        (ob.lo, db.hi, 0, th.lo), (0, 1, 0, 0), (0, 0, 1, 0), (1, 0, 0, 1),
    ]:
        out.append({"opening_balance": opening, "deposits": dep, "withdrawals": wd,
                    "overdraft_threshold": threshold,
                    "overdraft_fee": fees[len(out) % len(fees)]})
    return out


_EDGES = {
    "loan_calc": _loan_edges,
    "compound_int": _compound_edges,
    "daily_ledger": _ledger_edges,
}


# ---------------------------------------------------------------------------
# Section 4: Hypothesis strategies (random fill)
# ---------------------------------------------------------------------------

def _field_strategy(f: _Field) -> st.SearchStrategy[int]:
    """Integer units in [lo, hi]; biased toward bounds and tiny values."""
    # Kept to two branches: strategy depth is the main per-draw cost.
    bounds = sorted({f.lo, min(f.hi, f.lo + 1), max(f.lo, f.hi - 1), f.hi})
    return st.one_of(st.integers(f.lo, f.hi), st.sampled_from(bounds))


def _ledger_strategy(fs: dict[str, _Field]) -> st.SearchStrategy[dict[str, int]]:
    base = st.fixed_dictionaries({n: _field_strategy(f) for n, f in fs.items()})
    delta = st.sampled_from([-2, -1, 0, 1, 2])
    th = fs["overdraft_threshold"]

    @st.composite
    def draw_row(draw):
        row = dict(draw(base))
        if draw(st.booleans()):  # pin the threshold next to the net balance
            net = row["opening_balance"] + row["deposits"] - row["withdrawals"]
            row["overdraft_threshold"] = min(max(net + draw(delta), th.lo), th.hi)
        return row

    return draw_row()


def _random_strategy(preset_id: str, fs: dict[str, _Field]) -> st.SearchStrategy[dict[str, int]]:
    if preset_id == "daily_ledger":
        return _ledger_strategy(fs)
    return st.fixed_dictionaries({n: _field_strategy(f) for n, f in fs.items()})


HYPOTHESIS_CASES = 20   # rows drawn through Hypothesis per generate_cases call


def _rng_units(rng: random.Random, f: _Field) -> int:
    """Cheap biased draw of integer units in [lo, hi]: bounds, uniform, or log-uniform."""
    roll = rng.random()
    if roll < 0.10:  # float-ok: Hypothesis-style sampling weight
        return rng.choice((f.lo, f.lo + 1, f.hi - 1, f.hi, 0 if f.lo <= 0 <= f.hi else f.lo))
    span = max(abs(f.lo), abs(f.hi))
    if roll < 0.30:  # float-ok: sampling weight
        return rng.randint(f.lo, f.hi)
    mag = rng.getrandbits(rng.randint(1, max(1, span.bit_length())))
    value = -mag if (f.lo < 0 and rng.random() < 0.5) else mag  # float-ok: sampling weight
    return min(max(value, f.lo), f.hi)


# Rates (thousandths of a percent) that stress rounding: repeating decimals and x.xx5.
_RATE_FAVOURITES = (3_333, 6_667, 8_333, 1_667, 2_125, 4_375, 5_005, 12_345, 999, 7_875)


def _rng_row(preset_id: str, ordered: list[_Field], by_name: dict[str, _Field],
             rng: random.Random) -> dict[str, int]:
    row = {f.name: _rng_units(rng, f) for f in ordered}
    if "annual_rate_pct" in row and rng.random() < 0.25:  # float-ok: sampling weight
        row["annual_rate_pct"] = rng.choice(_RATE_FAVOURITES)
    if preset_id == "daily_ledger" and rng.random() < 0.5:  # float-ok: sampling weight
        th = by_name["overdraft_threshold"]
        net = row["opening_balance"] + row["deposits"] - row["withdrawals"]
        row["overdraft_threshold"] = min(max(net + rng.randint(-2, 2), th.lo), th.hi)
    return row


def _draw_random(strategy: st.SearchStrategy, count: int, seed: int | None) -> list[dict[str, int]]:
    """Collect ``count`` examples through ``@given`` (no example database)."""
    # Per-example engine overhead dominates on a 0.1 CPU, so each Hypothesis
    # example draws a chunk of rows instead of a single row.
    drawn: list[dict[str, int]] = []
    chunk = 20
    examples = -(-count // chunk)

    @settings(
        database=None,
        max_examples=examples,
        phases=[Phase.generate],
        deadline=None,
        suppress_health_check=list(HealthCheck),
    )
    @given(st.lists(strategy, min_size=chunk, max_size=chunk))
    def collect(rows):
        drawn.extend(rows)

    if seed is not None:
        collect = hyp_seed(seed)(collect)
    collect()
    return drawn


# ---------------------------------------------------------------------------
# Section 5: public API
# ---------------------------------------------------------------------------

def generate_cases(preset_id: str, num_cases: int, seed: int | None = None) -> list[Case]:
    """Return exactly ``num_cases`` unique Cases for ``preset_id``.

    Boundary cases (Requirement 11.3-11.7) come first, then Hypothesis-drawn
    cases fill the remainder. With ``seed`` the output is reproducible.
    """
    # Requirement 17.7 accepts 1..250 at the API; MIN_CASES (150) is only the default.
    if not 1 <= num_cases <= MAX_CASES:
        raise ValueError(f"num_cases must be between 1 and {MAX_CASES}")
    fields = _fields(preset_id)
    by_name = {f.name: f for f in fields}

    cases: list[Case] = []
    seen: set[tuple] = set()

    def add(units: dict[str, int]) -> None:
        case = _build(preset_id, fields, units)
        k = _key(case)
        if k not in seen and len(cases) < num_cases:
            seen.add(k)
            cases.append(case)

    for row in _EDGES[preset_id](by_name):
        add(row)

    # One small Hypothesis chunk (about 10 ms per row on 0.1 CPU, so it is kept
    # short), then cheap seeded ``random.Random`` rows for the bulk.
    if len(cases) < num_cases:
        strategy = _random_strategy(preset_id, by_name)
        for row in _draw_random(strategy, HYPOTHESIS_CASES, seed):
            add(row)
    rng = random.Random(seed)
    ordered = list(by_name.values())
    attempts = 0
    while len(cases) < num_cases and attempts < 20 * num_cases:
        attempts += 1
        add(_rng_row(preset_id, ordered, by_name, rng))
    if len(cases) < num_cases:  # pragma: no cover - ranges are far larger than 250
        raise ValueError("could not generate enough unique cases")
    return cases


# ---------------------------------------------------------------------------
# Section 6: Restricted_Namespace loader for user-supplied python_code (7.2)
# ---------------------------------------------------------------------------
#
# Entry-point contract (design.md leaves the name open): the user code defines
# a function named ``calculate``; if it does not, the code must define exactly
# one top-level function and that one is used. The function is called with the
# Case inputs as keyword arguments (``Decimal`` for scaled fields, ``int`` for
# integer fields) and must return a ``dict`` of output name -> ``Decimal``/``int``.
#
# Deviation from design.md: code is exec'd with ONE namespace dict (globals ==
# locals) instead of ``exec(code, globals, {})``; with a separate locals dict,
# module-level helper functions could not see each other.
#
# Defense layers: (1) AST pre-check, (2) near-empty ``__builtins__`` (only the
# pure names in ``_SAFE_BUILTINS``), (3) execution in a forked child process
# that is killed at the deadline, with an address-space cap. A thread cannot
# be killed, so a worker thread would keep spinning after an infinite loop and
# ``signal.alarm`` only works in the main thread.

import ast
import os
import resource
import multiprocessing
import time
from decimal import ROUND_DOWN, ROUND_HALF_UP, Context, InvalidOperation
from typing import Any, Callable

USER_CODE_TIMEOUT_S = 5.0  # float-ok: subprocess timeout
MAX_PYTHON_CODE_CHARS = 64 * 1024
_ENTRY_NAME = "calculate"
_MEMORY_HEADROOM_BYTES = 96 * 1024 * 1024

_FORBIDDEN_NAMES = frozenset({
    "eval", "exec", "open", "__import__", "compile", "globals", "locals", "vars",
    "getattr", "setattr", "delattr", "type", "input", "breakpoint", "help",
    "memoryview", "super", "object",
})
# Frame / code / generator introspection attributes that reach real globals
# without being dunder names (e.g. ``(x for x in ()).gi_frame.f_back``).
_FORBIDDEN_ATTR_PREFIXES = ("gi_", "ag_", "cr_", "f_", "co_", "tb_", "func_")
_FORBIDDEN_ATTRS = frozenset({"mro", "format", "format_map"})  # format: "{0.__class__}" probing

# Pure, side-effect-free builtins that ordinary Decimal code needs.
_SAFE_BUILTINS: dict[str, Any] = {
    "abs": abs, "min": min, "max": max, "int": int, "len": len, "range": range,
    "sum": sum, "divmod": divmod, "round": round, "bool": bool,
}

# Fixed context: generous precision, never trapping silently into float-like loss.
_FIXED_CONTEXT = Context(prec=60, rounding=ROUND_HALF_UP)


class RestrictedCodeError(ValueError):
    """User python_code was rejected before or while loading."""


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def check_user_code_ast(tree: ast.AST) -> None:
    """Reject forbidden constructs; raises RestrictedCodeError with the line."""
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise RestrictedCodeError(f"line {line}: import is not allowed")
        if isinstance(node, ast.Name):
            if _is_dunder(node.id) or node.id in _FORBIDDEN_NAMES:
                raise RestrictedCodeError(f"line {line}: name {node.id!r} is not allowed")
        elif isinstance(node, ast.Attribute):
            a = node.attr
            if (_is_dunder(a) or a in _FORBIDDEN_ATTRS or a in _FORBIDDEN_NAMES
                    or a.startswith(_FORBIDDEN_ATTR_PREFIXES)):
                raise RestrictedCodeError(f"line {line}: attribute {a!r} is not allowed")
        elif isinstance(node, (ast.ClassDef, ast.Global, ast.Nonlocal, ast.Await,
                               ast.AsyncFunctionDef, ast.Yield, ast.YieldFrom,
                               ast.GeneratorExp)):
            raise RestrictedCodeError(
                f"line {line}: {type(node).__name__} is not allowed")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in _FORBIDDEN_NAMES:  # pragma: no cover - Name rule hits first
            raise RestrictedCodeError(f"line {line}: call to {node.func.id!r} is not allowed")


def load_restricted_function(python_code: str) -> Callable[..., dict[str, Any]]:
    """AST-check, compile and exec ``python_code``; return its entry function.

    Runs in the calling process. ``run_user_python`` calls this inside a
    disposable child; call it directly only for trusted code or tests.
    """
    if not isinstance(python_code, str) or len(python_code) > MAX_PYTHON_CODE_CHARS:
        raise RestrictedCodeError("python_code missing or too large")
    try:
        tree = ast.parse(python_code, filename="<python_code>", mode="exec")
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        raise RestrictedCodeError(f"syntax error: {exc}") from None
    check_user_code_ast(tree)
    code_obj = compile(tree, "<python_code>", "exec")
    namespace: dict[str, Any] = {
        "Decimal": Decimal,
        "ROUND_HALF_UP": ROUND_HALF_UP,
        "ROUND_DOWN": ROUND_DOWN,
        "Context": _FIXED_CONTEXT,
        "__builtins__": dict(_SAFE_BUILTINS),
    }
    exec(code_obj, namespace)  # noqa: S102 - guarded by AST check + builtins + child process
    fn = namespace.get(_ENTRY_NAME)
    if not callable(fn):
        funcs = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
        if len(funcs) != 1:
            raise RestrictedCodeError(
                f"python_code must define a function named {_ENTRY_NAME!r} "
                "(or exactly one top-level function)")
        fn = namespace[funcs[0]]
    return fn


@dataclass(frozen=True)
class UserCaseResult:
    """Outcome of one Case against user python_code."""
    outputs: dict[str, Decimal] | None = None
    error: str | None = None      # reported as likely_cause="python_error"


def _normalize_outputs(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict) or not raw:
        raise TypeError("function must return a non-empty dict")
    out: dict[str, str] = {}
    for k, v in raw.items():
        if not isinstance(k, str):
            raise TypeError("output names must be strings")
        if isinstance(v, bool) or not isinstance(v, (Decimal, int)):
            raise TypeError(f"output {k!r} must be Decimal or int, got {type(v).__name__}")
        out[k] = str(Decimal(v))
    return out


def _child_main(conn, python_code: str, case_inputs: list[dict[str, Any]]) -> None:
    try:
        try:  # cap address space so memory bombs fail instead of OOM-killing the host
            with open("/proc/self/statm") as fh:
                vm = int(fh.read().split()[0]) * os.sysconf("SC_PAGE_SIZE")
            limit = vm + _MEMORY_HEADROOM_BYTES
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        except (OSError, ValueError):
            pass
        try:
            fn = load_restricted_function(python_code)
        except BaseException as exc:  # noqa: BLE001
            conn.send_bytes(json.dumps({"fatal": f"{type(exc).__name__}: {exc}"}).encode())
            return
        for i, inputs in enumerate(case_inputs):
            try:
                msg = {"i": i, "out": _normalize_outputs(fn(**inputs))}
            except BaseException as exc:  # noqa: BLE001 - per-case isolation
                msg = {"i": i, "err": f"{type(exc).__name__}: {exc}"[:300]}
            conn.send_bytes(json.dumps(msg).encode())
    finally:
        try:
            conn.close()
        finally:
            os._exit(0)


def run_user_python(python_code: str, cases: list[Case],
                    timeout_s: float = USER_CODE_TIMEOUT_S) -> list[UserCaseResult]:  # float-ok: timeout seconds
    """Run ``python_code`` against every Case; one result per Case, in order.

    The code runs in a forked child with a hard ``timeout_s`` deadline for the
    whole batch. Cases not finished by the deadline (the hung one and all after
    it) get a ``python_error`` result; the child is killed.
    """
    n = len(cases)
    results: list[UserCaseResult | None] = [None] * n
    ctx = multiprocessing.get_context("fork")
    recv, send = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child_main, args=(send, python_code,
                                                  [dict(c.inputs) for c in cases]), daemon=True)
    fatal: str | None = None
    deadline = time.monotonic() + timeout_s
    proc.start()
    send.close()
    try:
        done = 0
        while done < n:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not recv.poll(remaining):
                break
            try:
                msg = json.loads(recv.recv_bytes(1 << 20))
            except (EOFError, OSError, ValueError):
                break
            if "fatal" in msg:
                fatal = msg["fatal"]
                break
            i = msg["i"]
            if "out" in msg:
                try:
                    results[i] = UserCaseResult(
                        outputs={k: Decimal(v) for k, v in msg["out"].items()})
                except InvalidOperation:
                    results[i] = UserCaseResult(error="invalid output value")
            else:
                results[i] = UserCaseResult(error=msg["err"])
            done += 1
    finally:
        if proc.is_alive():
            proc.kill()
        proc.join(1.0)  # float-ok: join timeout
        recv.close()
    default = fatal or f"python_code exceeded {timeout_s:g} s or terminated abnormally"
    return [r if r is not None else UserCaseResult(error=default) for r in results]


# ---------------------------------------------------------------------------
# Section 7: differential verification and Parity_Report (7.3)
# ---------------------------------------------------------------------------

from decimal import ROUND_HALF_EVEN  # noqa: E402

from .cobol_runner import CobolRunner, validate_source  # noqa: E402
from . import reference_models  # noqa: E402

MAX_COUNTEREXAMPLES = 5
MAX_RAW_SAMPLE_LINES = 20
LIKELY_CAUSES = frozenset({
    "float_drift", "rounded_vs_truncation", "scale_mismatch",
    "overflow", "python_error", "unknown",
})

# Output columns of each preset's Batch_Protocol row, in order.
_OUTPUT_FIELDS: dict[str, tuple[str, ...]] = {
    "loan_calc": ("monthly_payment", "total_interest", "total_payable"),
    "compound_int": ("maturity_amount", "accrued_interest"),
    "daily_ledger": ("closing_balance", "fee_applied", "overdraft_flag"),
}

# Explicit contexts (never the ambient one). NaN/Infinity from user code must
# compare as "different" instead of raising, hence no traps in the delta context.
_DELTA_CONTEXT = Context(prec=60, rounding=ROUND_HALF_EVEN, traps=[])
_SCORE_CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)
_ZERO_DELTA = Decimal("0.00")
_PENNY = Decimal("0.01")
_TWO_PLACES = Decimal("0.01")


class VerificationError(Exception):
    """The run could not produce a Parity_Report (not a parity failure).

    ``kind`` is one of: invalid_request, rejected, compile_failed, run_timeout,
    run_failed, output_truncated, row_count_mismatch.
    """

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "message": self.message}


@dataclass(frozen=True)
class Counterexample:
    inputs: dict[str, str]
    cobol_output: dict[str, str]
    python_output: dict[str, str]
    differing_field: str
    penny_delta: str
    likely_cause: str  # one of LIKELY_CAUSES

    def to_dict(self) -> dict[str, Any]:
        return {
            "inputs": dict(self.inputs),
            "cobol_output": dict(self.cobol_output),
            "python_output": dict(self.python_output),
            "differing_field": self.differing_field,
            "penny_delta": self.penny_delta,
            "likely_cause": self.likely_cause,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Counterexample":
        return cls(
            inputs=dict(data["inputs"]),
            cobol_output=dict(data["cobol_output"]),
            python_output=dict(data["python_output"]),
            differing_field=data["differing_field"],
            penny_delta=data["penny_delta"],
            likely_cause=data["likely_cause"],
        )


def compute_parity_score(passed_cases: int, total_cases: int) -> str:
    """``passed / total * 100`` in Decimal, quantized to 2 places (Req 13.2)."""
    if total_cases <= 0:
        return "0.00"
    ratio = _SCORE_CONTEXT.divide(Decimal(passed_cases), Decimal(total_cases))
    pct = _SCORE_CONTEXT.multiply(ratio, Decimal(100))
    return format(pct.quantize(_TWO_PLACES, context=_SCORE_CONTEXT), "f")


@dataclass(frozen=True)
class ParityReport:
    total_cases: int
    passed_cases: int
    failed_cases: int
    parity_score_pct: str          # Decimal serialized as a string, 2 places
    verified: bool
    execution_ms: float  # float-ok: timing
    raw_cobol_sample_output: list[str] = field(default_factory=list)   # at most 20 lines
    counterexamples: list[Counterexample] = field(default_factory=list)  # at most 5

    @classmethod
    def build(cls, passed_cases: int, failed_cases: int, execution_ms: float,  # float-ok: timing
              raw_cobol_sample_output: list[str],
              counterexamples: list[Counterexample]) -> "ParityReport":
        total = passed_cases + failed_cases
        return cls(
            total_cases=total,
            passed_cases=passed_cases,
            failed_cases=failed_cases,
            parity_score_pct=compute_parity_score(passed_cases, total),
            verified=failed_cases == 0 and total > 0,
            execution_ms=round(execution_ms, 1),
            raw_cobol_sample_output=list(raw_cobol_sample_output[:MAX_RAW_SAMPLE_LINES]),
            counterexamples=list(counterexamples[:MAX_COUNTEREXAMPLES]),
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict. Every money value is a string; counts are ints."""
        return {
            "total_cases": self.total_cases,
            "passed_cases": self.passed_cases,
            "failed_cases": self.failed_cases,
            "parity_score_pct": self.parity_score_pct,
            "verified": self.verified,
            "execution_ms": self.execution_ms,
            "raw_cobol_sample_output": list(self.raw_cobol_sample_output),
            "counterexamples": [c.to_dict() for c in self.counterexamples],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ParityReport":
        # execution_ms is a timing, not money: a JSON number parsed with
        # parse_float=Decimal is turned back into a plain number here.
        ms = data["execution_ms"]
        return cls(
            total_cases=int(data["total_cases"]),
            passed_cases=int(data["passed_cases"]),
            failed_cases=int(data["failed_cases"]),
            parity_score_pct=data["parity_score_pct"],
            verified=bool(data["verified"]),
            execution_ms=float(ms) if isinstance(ms, Decimal) else ms,  # float-ok: timing ms
            raw_cobol_sample_output=list(data["raw_cobol_sample_output"]),
            counterexamples=[Counterexample.from_dict(c) for c in data["counterexamples"]],
        )

    @classmethod
    def from_json(cls, text: str) -> "ParityReport":
        return cls.from_dict(json.loads(text, parse_float=Decimal))


# -- helpers ----------------------------------------------------------------

_SHARED_RUNNER: CobolRunner | None = None


def _shared_runner() -> CobolRunner:
    """Process-wide runner, so compiled preset binaries are reused."""
    global _SHARED_RUNNER
    if _SHARED_RUNNER is None:
        _SHARED_RUNNER = CobolRunner()
    return _SHARED_RUNNER


def _fmt(value: Decimal | int) -> str:
    return format(Decimal(value), "f")


def _format_row(case: Case, fields: list[_Field]) -> str:
    parts = []
    for f in fields:
        v = case.inputs[f.name]
        parts.append(str(v) if f.scale == 0 else f"{Decimal(v):.{f.scale}f}")
    return " ".join(parts)


def _penny_delta_text(delta: Decimal) -> str:
    if delta.is_nan() or delta.is_infinite():
        return str(delta)
    if delta.as_tuple().exponent > -2:
        delta = delta.quantize(_PENNY, context=_DELTA_CONTEXT)
    return format(delta, "f")


def _classify(cobol: Decimal, py: Decimal, delta: Decimal, float_bug: bool) -> str:
    """Heuristic likely-cause label for one differing field."""
    if cobol.is_nan() or py.is_nan() or py.is_infinite():
        return "unknown"
    ctx = _DELTA_CONTEXT
    big = Decimal(10) ** 5
    # Python value cannot fit any PIC, or the two differ by whole high-order digits.
    if abs(py) >= Decimal(10) ** 9 or (
        delta >= big and ctx.remainder(delta, big) == 0
    ):
        return "overflow"
    if float_bug:
        return "float_drift"
    exp = py.as_tuple().exponent
    if isinstance(exp, int) and exp < -2 and py != py.quantize(_PENNY, context=ctx):
        return "scale_mismatch"
    if cobol != 0 and py != 0:
        ratio = ctx.divide(py, cobol)
        if ratio > 0 and ratio != 1:
            r = ratio.normalize(ctx)
            digits = r.as_tuple().digits
            if digits == (1,):  # a pure power of ten other than 1
                return "scale_mismatch"
    if delta == _PENNY:
        return "rounded_vs_truncation"
    return "unknown"


def _compare_case(
    case: Case,
    inputs_text: dict[str, str],
    out_names: tuple[str, ...],
    cobol_line: str,
    py_out: dict[str, Decimal] | None,
    py_error: str | None,
    float_bug: bool,
) -> Counterexample | None:
    """Return None when every field has Penny_Delta == 0.00, else a Counterexample."""
    tokens = cobol_line.split()
    cobol_vals: dict[str, Decimal] = {}
    cobol_text = {n: (tokens[i] if i < len(tokens) else "") for i, n in enumerate(out_names)}
    parse_problem: str | None = None
    if len(tokens) != len(out_names):
        parse_problem = out_names[0]
    for n in out_names:
        tok = cobol_text[n]
        try:
            cobol_vals[n] = Decimal(tok)  # exact; never float
        except Exception:  # noqa: BLE001 - InvalidOperation / empty token
            if parse_problem is None:
                parse_problem = n
    cobol_shown = {n: (_fmt(cobol_vals[n]) if n in cobol_vals else cobol_text[n])
                   for n in out_names}

    if py_error is not None or py_out is None:
        return Counterexample(
            inputs=inputs_text, cobol_output=cobol_shown,
            python_output={"error": py_error or "no output"},
            differing_field="(python)", penny_delta="n/a", likely_cause="python_error",
        )
    py_shown = {n: (_fmt(py_out[n]) if n in py_out else "<missing>") for n in out_names}
    if parse_problem is not None:
        return Counterexample(
            inputs=inputs_text, cobol_output=cobol_shown, python_output=py_shown,
            differing_field=parse_problem, penny_delta="n/a", likely_cause="unknown",
        )
    for n in out_names:
        if n not in py_out:
            return Counterexample(
                inputs=inputs_text, cobol_output=cobol_shown, python_output=py_shown,
                differing_field=n, penny_delta="n/a", likely_cause="python_error",
            )
        py = Decimal(py_out[n])
        delta = abs(_DELTA_CONTEXT.subtract(cobol_vals[n], py))
        if not (delta == _ZERO_DELTA):  # NaN compares unequal, so it fails
            return Counterexample(
                inputs=inputs_text, cobol_output=cobol_shown, python_output=py_shown,
                differing_field=n, penny_delta=_penny_delta_text(delta),
                likely_cause=_classify(cobol_vals[n], py, delta, float_bug),
            )
    return None


def _run_target(
    preset_id: str,
    cases: list[Case],
    target_fn: Callable[..., dict[str, Decimal]] | None,
    python_code: str | None,
    simulate_float_bug: bool,
) -> list[tuple[dict[str, Decimal] | None, str | None]]:
    """One (outputs, error) pair per Case, in order."""
    if python_code is not None:
        try:
            return [(r.outputs, r.error) for r in run_user_python(python_code, cases)]
        except Exception as exc:  # noqa: BLE001 - e.g. fork unavailable
            msg = f"{type(exc).__name__}: {exc}"[:300]
            return [(None, msg) for _ in cases]
    fn = target_fn
    if fn is None:
        registry = (reference_models.FLOAT_BUG_REGISTRY if simulate_float_bug
                    else reference_models.REGISTRY)
        fn = registry[preset_id]
    results: list[tuple[dict[str, Decimal] | None, str | None]] = []
    for case in cases:
        try:
            out = fn(**case.inputs)
            if not isinstance(out, dict):
                raise TypeError("target function must return a dict")
            results.append((out, None))
        except Exception as exc:  # noqa: BLE001 - per-case isolation
            results.append((None, f"{type(exc).__name__}: {exc}"[:300]))
    return results


def run_verification(
    preset_id: str | None,
    cobol_source: str | None,
    target_fn: Callable[..., dict[str, Decimal]] | None = None,
    num_cases: int = MIN_CASES,
    seed: int | None = None,
    *,
    python_code: str | None = None,
    simulate_float_bug: bool = False,
    runner: CobolRunner | None = None,
) -> ParityReport:
    """Differentially verify a COBOL program against a Python target.

    ``preset_id`` selects the input schema used to generate Cases (and the
    output column layout). With ``cobol_source`` the pasted program is compiled
    and run instead of the bundled preset binary; its Batch_Protocol rows must
    match the preset's layout. The target is, in priority order, ``python_code``
    (restricted loader), ``target_fn``, the Float_Bug_Demo when
    ``simulate_float_bug`` is set, else the preset's Reference_Model.

    Raises ``VerificationError`` when no report can be produced (rejected or
    non-compiling source, timeout, or an output row count that differs from the
    Case count).
    """
    start = time.perf_counter()
    if preset_id is None or preset_id not in _OUTPUT_FIELDS:
        raise VerificationError("invalid_request", f"unknown or missing preset_id: {preset_id!r}")
    try:
        cases = generate_cases(preset_id, num_cases, seed)
    except ValueError as exc:
        raise VerificationError("invalid_request", str(exc)) from None
    fields = _fields(preset_id)
    out_names = _OUTPUT_FIELDS[preset_id]
    # Never a blank line: daily_ledger treats a blank line as end of input.
    rows = [_format_row(c, fields) for c in cases]
    runner = runner or _shared_runner()

    # --- exactly one COBOL run for all Cases (Req 12.1) ---
    if cobol_source is not None:
        verdict = validate_source(cobol_source)
        if not verdict.accepted:
            msg = verdict.error.message if verdict.error else "source rejected"
            raise VerificationError("rejected", msg)
        compiled, run = runner.compile_and_run_custom(cobol_source, rows)
        if not compiled.success or run is None:
            detail = (compiled.stderr or "compile failed").strip()[:2000]
            raise VerificationError("compile_failed", detail)
    else:
        run = runner.compile_and_run_preset(preset_id, rows)
    if run.timed_out:
        raise VerificationError("run_timeout", "COBOL run exceeded 5 seconds")
    if run.truncated:
        raise VerificationError("output_truncated", "COBOL output exceeded the size limit")
    if run.exit_code != 0:
        raise VerificationError(
            "run_failed", f"COBOL exited with code {run.exit_code}: {run.stderr.strip()[:500]}")
    lines = run.stdout.splitlines()
    if len(lines) != len(cases):
        raise VerificationError(
            "row_count_mismatch",
            f"COBOL produced {len(lines)} output rows for {len(cases)} cases")

    # --- same Cases through the target (Req 12.2) ---
    targets = _run_target(preset_id, cases, target_fn, python_code, simulate_float_bug)
    float_bug = simulate_float_bug and python_code is None and target_fn is None

    passed = 0
    failed = 0
    counterexamples: list[Counterexample] = []
    for case, line, (py_out, py_err) in zip(cases, lines, targets):
        inputs_text = {k: _fmt(v) for k, v in case.inputs.items()}
        ce = _compare_case(case, inputs_text, out_names, line, py_out, py_err, float_bug)
        if ce is None:
            passed += 1
        else:
            failed += 1
            if len(counterexamples) < MAX_COUNTEREXAMPLES:
                counterexamples.append(ce)

    return ParityReport.build(
        passed, failed, (time.perf_counter() - start) * 1000.0,  # float-ok: timing ms
        lines[:MAX_RAW_SAMPLE_LINES], counterexamples,
    )
