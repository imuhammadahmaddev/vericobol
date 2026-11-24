---
inclusion: always
---

# VeriCOBOL: Product Vision

Kiroween 2026 Hackathon submission, Resurrection category.

**Tagline:** Resurrect 1959 COBOL banking systems into modern cloud dashboards, with mathematical penny-exact proof.

## Problem
Banks still run core lending, interest, and ledger math on legacy COBOL. Teams fear rewriting COBOL with LLMs because one floating-point or rounding hallucination (for example, $10.005 rounding the wrong way) causes compliance failures and financial loss.

## Solution
VeriCOBOL takes legacy COBOL, runs a 3-agent modernization pipeline (Legacy Parser -> Cloud Architect -> Parity Auditor), and generates exact-precision Python plus an interactive dashboard schema. It then proves penny-exact equivalence by compiling and executing the REAL original COBOL program with GnuCOBOL (`cobc`) side by side with the generated Python function, using Differential Property-Based Testing (Hypothesis).

## Zero-Login UX
- Public web URL. No login, no signup, no database.
- Users can click a preset or paste custom COBOL.

## Three Preset Programs (static files, bundled)
1. **Loan Amortization Calculator** (`loan_calc.cbl`): principal, annual rate, term in months -> monthly payment, total interest, total payable.
2. **Compound Interest Engine** (`compound_int.cbl`): principal, annual rate, compounding frequency, years -> maturity amount, accrued interest.
3. **Daily Ledger Reconciler** (`daily_ledger.cbl`): opening balance, deposits, withdrawals, overdraft threshold and fee -> closing balance, fee applied, overdraft flag.

## Split-Screen UI
- **Left, "Legacy Mainframe":** IBM 3270 style, background `#0A0E0C`, phosphor-green `#00FF66` monospace. Shows COBOL source and raw GnuCOBOL stdout.
- **Center/Bottom, "Mathematical Parity Proof":** live differential PBT results, for example `200 / 200 Exact Penny Matches — 0 Hallucinations`, plus an inspectable counterexample diff log of edge-case bugs caught (float drift, COBOL `ROUNDED` vs truncation) and auto-repaired.
- **Right, "Resurrected Cloud Dashboard":** modern fintech dashboard with sliders and inputs wired to the verified Python API, live charts, plain-English extracted business rules, and a "Download Verified Python + API" button.

## Cold-Start UX
On page load the frontend silently pings `GET {NEXT_PUBLIC_API_URL}/health` and shows a themed status bar ("Establishing 3270 Mainframe Connection... Warming up GnuCOBOL compiler...") to mask Render Free Tier's 30-60 s cold start.

## 3-Agent Pipeline
1. **legacy-parser:** parses `DATA DIVISION` and `PIC` clauses, input/output contracts, and plain-English banking rules.
2. **cloud-architect:** generates typed Python 3.12 using `decimal.Decimal` (matching PIC scale and rounding rules) plus JSON UI metadata for the dashboard.
3. **parity-auditor:** compiles and runs real GnuCOBOL against the generated Python via Hypothesis (150-200 randomized edge cases: $0.00, huge values, negative/overdraft thresholds, repeating decimals like 3.333%). On any discrepancy > $0.00 it returns the exact failing counterexample for self-healing retry (max 2 retries) until 100% penny parity passes.

## Success Criteria
- Every shown result is backed by a live GnuCOBOL comparison.
- Repeated judge clicks cost $0 in LLM tokens (cached presets) while still running live PBT.
