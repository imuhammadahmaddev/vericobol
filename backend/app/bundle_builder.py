"""Build ``cobol_samples/preset_bundles.json`` (run: ``python -m app.bundle_builder``).

One entry per preset and per ``demonstrate_self_healing`` value ("false", "true").
Verified code is the Decimal reference source; the dashboard metadata, PIC mappings
and export module are derived from the preset contract. An entry is written only
after a live ``run_verification`` against GnuCOBOL returns parity 100.00. Any
generated code or export containing ``float`` is rejected.

Output is deterministic: sorted keys, Decimals as strings, fixed stage records.
"""

from __future__ import annotations

import json
import re
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from .export_template import render
from .pbt_verifier import run_verification
from .resurrection import (
    BUNDLES_PATH,
    PRESET_IDS,
    build_dashboard_metadata,
    build_pic_mappings,
    contract_from_preset,
    reference_decimal_source,
)

NUM_CASES = 200
FLAGS = ("false", "true")
_FLOAT_RE = re.compile(r"float", re.IGNORECASE)


class BundleError(Exception):
    """A bundle entry cannot be built or certified."""


BUSINESS_RULES: dict[str, list[str]] = {
    "loan_calc": [
        "Inputs are the loan principal (up to 9 digits plus 2 decimals), the annual interest rate "
        "as a percentage (up to 3 digits plus 3 decimals, for example 3.333) and the term in whole months (up to 3 digits).",
        "The monthly interest rate is the annual rate divided by 1200, that is annual percent divided by 12 months and by 100.",
        "When the term is 0 months, the monthly payment is 0.00.",
        "When the annual rate is 0, the monthly payment is the principal divided evenly by the number of months, rounded half-up to the cent.",
        "Otherwise the monthly payment is the standard amortization payment: principal times the monthly rate, "
        "divided by 1 minus (1 + monthly rate) raised to the power of minus the term. "
        "The whole expression is evaluated at full precision and rounded half-up (COBOL ROUNDED) once, when stored.",
        "Total payable is the already-rounded monthly payment multiplied by the term in months, rounded half-up to the cent.",
        "Total interest is total payable minus the principal, rounded half-up to the cent. Because the rounded payment is used, "
        "total interest can differ by a few cents from an unrounded calculation.",
        "All three results are stored in PIC 9(9)V99 fields: two decimal places, no sign, and nine integer digits. "
        "A value too large for the field loses its high-order digits, as it does on the mainframe.",
    ],
    "compound_int": [
        "Inputs are the principal (up to 9 digits plus 2 decimals), the annual rate as a percentage "
        "(up to 3 digits plus 3 decimals), the number of compounding periods per year (0 to 99) and the number of years (0 to 99).",
        "The number of periods is compounds per year multiplied by years.",
        "When the number of periods is 0, nothing compounds and the maturity amount equals the principal.",
        "Each period multiplies the running balance by (100 x periods per year + annual rate) divided by (100 x periods per year), "
        "which is 1 plus the annual rate per period.",
        "The running balance is stored rounded half-up (COBOL ROUNDED) to the cent after every period, so rounding "
        "differences accumulate period by period exactly as in the legacy program.",
        "Maturity amount is the running balance after the last period, in PIC 9(9)V99 (two decimals, no sign).",
        "Accrued interest is maturity amount minus the original principal, rounded half-up, in a signed PIC S9(9)V99 field.",
        "A balance that grows beyond nine integer digits loses its high-order digits, as in COBOL.",
    ],
    "daily_ledger": [
        "Inputs are the opening balance (signed), the day's total deposits, the day's total withdrawals, "
        "the overdraft threshold (signed, so it can be negative) and the overdraft fee, all in two-decimal amounts.",
        "The day's balance is the opening balance plus deposits minus withdrawals.",
        "If that balance is strictly below the overdraft threshold, the overdraft fee is applied and the overdraft flag is 1. "
        "A balance exactly equal to the threshold is not an overdraft.",
        "Otherwise the fee applied is 0.00 and the overdraft flag is 0.",
        "The closing balance is the day's balance minus the fee applied. It is signed and can go negative.",
        "No ROUNDED clause is used anywhere: every store truncates toward zero (COBOL default). All amounts have two decimals, "
        "so no rounding actually occurs for valid inputs.",
        "Fee applied is stored in PIC 9(5)V99 and the flag in PIC 9; the closing balance is stored in PIC S9(9)V99.",
    ],
}


def _reject_float(label: str, text: str) -> None:
    match = _FLOAT_RE.search(text)
    if match:
        line = text.count("\n", 0, match.start()) + 1
        raise BundleError(f"{label} contains 'float' (line {line})")


def _stages() -> list[dict[str, Any]]:
    return [
        {"name": "legacy_parser", "status": "ok", "elapsed_ms": 0, "source": "cache"},
        {"name": "cloud_architect", "status": "ok", "elapsed_ms": 0, "source": "cache"},
    ]


def build_entry(preset_id: str) -> dict[str, Any]:
    """Build and certify one entry. Raises ``BundleError`` unless parity is 100.00."""
    if preset_id not in BUSINESS_RULES:
        raise BundleError(f"unknown preset_id {preset_id!r}")
    schema = contract_from_preset(preset_id)
    code = reference_decimal_source(preset_id)
    export = render(code, schema)
    _reject_float(f"{preset_id} verified_python_code", code)
    _reject_float(f"{preset_id} export_fastapi_code", export)

    report = run_verification(preset_id, None, num_cases=NUM_CASES, python_code=code)
    if not report.verified or Decimal(report.parity_score_pct) != Decimal("100.00"):
        raise BundleError(
            f"{preset_id}: live verification returned parity {report.parity_score_pct} "
            f"({report.passed_cases}/{report.total_cases}); refusing to write entry"
        )
    return {
        "business_rules": list(BUSINESS_RULES[preset_id]),
        "pic_mappings": build_pic_mappings(schema),
        "dashboard_metadata": build_dashboard_metadata(schema),
        "verified_python_code": code,
        "export_fastapi_code": export,
        "stages": _stages(),
    }


def _default(obj: Any) -> str:
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"cannot serialize {type(obj).__name__}")


def build_bundles() -> dict[str, dict[str, Any]]:
    bundles: dict[str, dict[str, Any]] = {}
    for preset_id in PRESET_IDS:
        entry = build_entry(preset_id)
        bundles[preset_id] = {
            flag: json.loads(json.dumps(entry, default=_default)) for flag in FLAGS
        }
    return bundles


def write_bundles(path: Path | None = None) -> Path:
    """Build every entry, then write the file. Nothing is written if any entry fails."""
    target = path or BUNDLES_PATH
    text = json.dumps(build_bundles(), indent=2, sort_keys=True, default=_default) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    return target


def main() -> int:
    try:
        target = write_bundles()
    except BundleError as exc:
        print(f"bundle_builder: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {target} ({len(PRESET_IDS) * len(FLAGS)} entries, all parity 100.00)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
