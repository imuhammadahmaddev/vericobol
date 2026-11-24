"""Reference_Models: exact ``Decimal`` models of the three preset COBOL programs.

The COBOL sources in ``backend/cobol_samples/`` are the ground truth. Every
model reproduces, bit for bit, what GnuCOBOL 3.2 does for the same row.

Findings from the empirical parity runs (hundreds of rows per program against
the real binaries, see task 4.1):

* Receiving fields without ``ON SIZE ERROR`` silently drop high-order digits
  (value modulo ``10 ** integer_digits``). Unsigned receiving fields also drop
  the sign (``PIC 9(9)V99`` stores the absolute value). Fractional digits that
  do not fit are truncated toward zero when the statement has no ``ROUNDED``.
* GnuCOBOL does not evaluate inline arithmetic with exact rationals. It uses
  GMP integers with a decimal scale, and ``cob_decimal_div`` works like this:
  ``scale = s1 - s2``; the dividend is shifted left by
  ``38 + max(0, -scale)`` decimal digits; the integer quotient is truncated
  toward zero. Multiplication, addition and integer powers are exact.
  ``loan_calc`` therefore needs the same truncation points, otherwise rare
  rows differ by one cent. The helpers ``_cob_div`` and ``_round_half_up``
  below implement exactly that on plain Python integers, which is exact and
  needs no float.
* ``x ** (0 - N)`` is computed as ``1 / (x ** N)`` with ``x ** N`` exact, then
  the division above (result truncated at 38 decimals).

All results are returned as ``Decimal`` values quantized to the scale of the
matching ``PIC`` clause. Every ``Decimal`` operation runs under
``localcontext(MONEY_CONTEXT)``; the ambient context is never used.
"""

from __future__ import annotations

from decimal import Context, Decimal, localcontext

# Explicit context, never the ambient one. 38 digits is GnuCOBOL's COB_MAX_DIGITS.
MONEY_CONTEXT = Context(prec=38)

_COB_MAX_DIGITS = 38


# --------------------------------------------------------------------------
# Fixed-point helpers (integer value + decimal scale, like GnuCOBOL's cob_decimal)
# --------------------------------------------------------------------------

def _scaled_trunc(value: Decimal, scale: int) -> int:
    """Return ``value * 10**scale`` truncated toward zero, as an exact int."""
    sign, digits, exp = value.as_tuple()
    if not isinstance(exp, int):
        raise ValueError("non-finite Decimal is not a valid money value")
    mantissa = int("".join(map(str, digits)) or "0")
    shift = exp + scale
    if shift >= 0:
        mantissa *= 10 ** shift
    else:
        mantissa //= 10 ** (-shift)
    return -mantissa if sign else mantissa


def _store_unsigned(value: Decimal, int_digits: int, scale: int) -> int:
    """Store into ``PIC 9(int_digits)V9(scale)``: drop sign, truncate, wrap."""
    return abs(_scaled_trunc(value, scale)) % 10 ** (int_digits + scale)


def _store_signed(value: Decimal, int_digits: int, scale: int) -> int:
    """Store into ``PIC S9(int_digits)V9(scale)``: truncate, wrap magnitude."""
    scaled = _scaled_trunc(value, scale)
    mag = abs(scaled) % 10 ** (int_digits + scale)
    return -mag if scaled < 0 else mag


def _wrap_unsigned(scaled: int, int_digits: int, scale: int) -> int:
    return abs(scaled) % 10 ** (int_digits + scale)


def _wrap_signed(scaled: int, int_digits: int, scale: int) -> int:
    mag = abs(scaled) % 10 ** (int_digits + scale)
    return -mag if scaled < 0 else mag


def _cob_div(n: int, n_scale: int, d: int, d_scale: int) -> tuple[int, int]:
    """GnuCOBOL ``cob_decimal_div`` for a positive divisor: (int, scale)."""
    scale = n_scale - d_scale
    shift = _COB_MAX_DIGITS + (-scale if scale < 0 else 0)
    num = n * 10 ** shift
    # Truncate toward zero (mpz_tdiv_q).
    q = abs(num) // abs(d)
    if (num < 0) != (d < 0):
        q = -q
    return q, scale + shift


def _round_half_up(value: int, scale: int, to_scale: int) -> int:
    """COBOL ``ROUNDED`` (nearest, ties away from zero) down to ``to_scale``."""
    if scale <= to_scale:
        return value * 10 ** (to_scale - scale)
    unit = 10 ** (scale - to_scale)
    mag, rem = divmod(abs(value), unit)
    if rem * 2 >= unit:
        mag += 1
    return -mag if value < 0 else mag


def _to_decimal(scaled: int, scale: int) -> Decimal:
    return Decimal(scaled).scaleb(-scale)


# --------------------------------------------------------------------------
# loan_calc
# --------------------------------------------------------------------------

def loan_calc(principal: Decimal, annual_rate_pct: Decimal, term_months: int) -> dict[str, Decimal]:
    """Mirror ``loan_calc.cbl``.

    Fields: principal 9(9)V99, rate 9(3)V999, term 9(3); outputs 9(9)V99, each
    stored ROUNDED (half-up).
    """
    with localcontext(MONEY_CONTEXT):
        p = _store_unsigned(principal, 9, 2)                      # scale 2
        r = _store_unsigned(annual_rate_pct, 3, 3)                # scale 3
        n = abs(int(term_months)) % 1000                          # 9(3)

        if n == 0:
            payment = 0
        elif r == 0:
            q, qs = _cob_div(p, 2, n, 0)
            payment = _round_half_up(q, qs, 2)
        else:
            # monthly rate = R / 1200, truncated at scale 41 (3 + 38).
            mr, mr_s = _cob_div(r, 3, 1200, 0)
            base = 10 ** mr_s + mr                                # 1 + R/1200
            # base ** (0 - N) = 1 / base**N: exact power, then cob_decimal_div,
            # which truncates at 38 decimals.
            power = base ** n
            inv = 10 ** (_COB_MAX_DIGITS + mr_s * n) // power     # scale 38
            denom = 10 ** _COB_MAX_DIGITS - inv                   # 1 - inv, scale 38
            numer = p * mr                                        # scale 2 + 41
            q, qs = _cob_div(numer, 2 + mr_s, denom, _COB_MAX_DIGITS)
            payment = _round_half_up(q, qs, 2)
        payment = _wrap_unsigned(payment, 9, 2)

        total = _wrap_unsigned(payment * n, 9, 2)                 # ROUNDED, exact at scale 2
        interest = _wrap_unsigned(total - p, 9, 2)                # unsigned: abs, wrap

        return {
            "monthly_payment": _to_decimal(payment, 2),
            "total_interest": _to_decimal(interest, 2),
            "total_payable": _to_decimal(total, 2),
        }


# --------------------------------------------------------------------------
# compound_int
# --------------------------------------------------------------------------

def compound_int(
    principal: Decimal, annual_rate_pct: Decimal, compounds_per_year: int, years: int
) -> dict[str, Decimal]:
    """Mirror ``compound_int.cbl``: the balance is stored ROUNDED every period."""
    with localcontext(MONEY_CONTEXT):
        p = _store_unsigned(principal, 9, 2)                      # scale 2
        r = _store_unsigned(annual_rate_pct, 3, 3)                # scale 3
        per_year = abs(int(compounds_per_year)) % 100             # 9(2)
        yrs = abs(int(years)) % 100                               # 9(2)
        periods = per_year * yrs                                  # 9(4), max 9801

        balance = p
        if periods > 0:
            denom = 100 * per_year * 1000                         # 9(6)V999, scale 3
            numer = denom + r                                     # 9(6)V999, scale 3
            for _ in range(periods):
                # balance (s2) * numer (s3) exact, / denom (s3) truncated at s40.
                q, qs = _cob_div(balance * numer, 5, denom, 3)
                balance = _wrap_unsigned(_round_half_up(q, qs, 2), 9, 2)

        maturity = balance
        accrued = _wrap_signed(maturity - p, 9, 2)                # S9(9)V99
        # The program displays accrued through ``PIC -(8)9.99``, which holds only
        # 8 integer digits (the first '-' is the sign position), one fewer than
        # the S9(9)V99 field. MOVE drops the 9th digit, so the observable
        # output wraps at 10**8. Empirically confirmed against the binary.
        accrued_shown = _wrap_signed(accrued, 8, 2)
        return {
            "maturity_amount": _to_decimal(maturity, 2),
            "accrued_interest": _to_decimal(accrued_shown, 2),
        }


# --------------------------------------------------------------------------
# daily_ledger
# --------------------------------------------------------------------------

def daily_ledger(
    opening_balance: Decimal,
    deposits: Decimal,
    withdrawals: Decimal,
    overdraft_threshold: Decimal = Decimal("0.00"),
    overdraft_fee: Decimal = Decimal("0.00"),
) -> dict[str, Decimal]:
    """Mirror ``daily_ledger.cbl``: no ROUNDED anywhere (truncation)."""
    with localcontext(MONEY_CONTEXT):
        opening = _store_signed(opening_balance, 9, 2)
        dep = _store_unsigned(deposits, 9, 2)
        wd = _store_unsigned(withdrawals, 9, 2)
        threshold = _store_signed(overdraft_threshold, 9, 2)
        fee = _store_unsigned(overdraft_fee, 5, 2)

        balance = _wrap_signed(opening + dep - wd, 9, 2)          # S9(9)V99
        if balance < threshold:
            fee_applied, flag = fee, 1
        else:
            fee_applied, flag = 0, 0
        closing = _wrap_signed(balance - fee_applied, 9, 2)       # S9(9)V99

        return {
            "closing_balance": _to_decimal(closing, 2),
            "fee_applied": _to_decimal(fee_applied, 2),
            "overdraft_flag": Decimal(flag),
        }


REGISTRY: dict[str, callable] = {
    "loan_calc": loan_calc,
    "compound_int": compound_int,
    "daily_ledger": daily_ledger,
}


# ==========================================================================
# FLOAT-BUG DEMONSTRATION MODELS  (deliberately WRONG, never used for money)
# --------------------------------------------------------------------------
# Everything below intentionally uses IEEE 754 floats, float math and naive
# ``round()``. These are the "typical LLM mistake" variants that the verifier
# must catch (Requirement 10.8). They return ``Decimal`` results with the same
# signatures as the exact models above. Nothing above this banner uses float.
# ==========================================================================

def _fb_money(x: float) -> Decimal:
    """Naive float -> 2-decimal Decimal: banker's ``round()`` on a float."""
    return Decimal(str(round(x, 2))).quantize(Decimal("0.01"))


def loan_calc_float_bug(
    principal: Decimal, annual_rate_pct: Decimal, term_months: int
) -> dict[str, Decimal]:
    """BUG: float annuity formula, float ``**``, banker's ``round()``."""
    p = float(principal)
    n = int(term_months)
    if n <= 0:
        payment = 0.0
    elif float(annual_rate_pct) == 0.0:
        payment = round(p / n, 2)
    else:
        mr = float(annual_rate_pct) / 100.0 / 12.0
        payment = round(p * mr / (1.0 - (1.0 + mr) ** (-n)), 2)
    total = round(payment * n, 2)
    interest = round(total - p, 2)
    return {
        "monthly_payment": _fb_money(payment),
        "total_interest": _fb_money(interest),
        "total_payable": _fb_money(total),
    }


def compound_int_float_bug(
    principal: Decimal, annual_rate_pct: Decimal, compounds_per_year: int, years: int
) -> dict[str, Decimal]:
    """BUG: float closed form ``P * (1 + r/k) ** (k*y)``, rounded once at the end.

    COBOL stores the balance ROUNDED every period, so the two drift apart.
    """
    p = float(principal)
    k = int(compounds_per_year)
    periods = k * int(years)
    balance = p
    if periods > 0:
        balance = round(p * (1.0 + float(annual_rate_pct) / 100.0 / k) ** periods, 2)
    return {
        "maturity_amount": _fb_money(balance),
        "accrued_interest": _fb_money(balance - p),
    }


def daily_ledger_float_bug(
    opening_balance: Decimal,
    deposits: Decimal,
    withdrawals: Decimal,
    overdraft_threshold: Decimal = Decimal("0.00"),
    overdraft_fee: Decimal = Decimal("0.00"),
) -> dict[str, Decimal]:
    """BUG: float balance, then float cents truncated with ``int()``.

    ``0.1 + 0.2`` style drift makes values like 0.29 become 28 cents.
    """
    balance = float(opening_balance) + float(deposits) - float(withdrawals)
    flag = 1 if balance < float(overdraft_threshold) else 0
    fee = float(overdraft_fee) if flag else 0.0
    closing_cents = int((balance - fee) * 100)
    fee_cents = int(fee * 100)
    return {
        "closing_balance": Decimal(closing_cents).scaleb(-2).quantize(Decimal("0.01")),
        "fee_applied": Decimal(fee_cents).scaleb(-2).quantize(Decimal("0.01")),
        "overdraft_flag": Decimal(flag),
    }


FLOAT_BUG_REGISTRY: dict[str, callable] = {
    "loan_calc": loan_calc_float_bug,
    "compound_int": compound_int_float_bug,
    "daily_ledger": daily_ledger_float_bug,
}
