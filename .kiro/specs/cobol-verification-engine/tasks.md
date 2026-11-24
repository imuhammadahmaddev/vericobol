# Implementation Plan: COBOL Verification Engine

## Overview

The design specifies Python 3.12, so no language prompt is needed. Work proceeds in this order: repository and Docker setup, COBOL presets and catalog, reference models, security validator and runner, PBT verifier, then the FastAPI endpoints. All monetary math uses `decimal.Decimal` (never `float`, except in the isolated `*_float_bug` functions). Every COBOL program is compiled once per verification and all cases are batched through one run. All paths use `pathlib.Path`. No `pandas`, `numpy`, `scipy`, or `torch`. Property tests use `hypothesis` with `database=None`.

## Tasks

- [x] 1. Set up repository and Docker runtime
  - [x] 1.1 Add `.gitattributes` at the repository root
    - Set `eol=lf` for `*.cbl`, `*.py`, `*.sh`, and `Dockerfile`
    - _Requirements: 1.1, 1.2_

  - [x] 1.2 Create backend scaffolding, `requirements.txt`, and `Dockerfile`
    - Create `backend/app/__init__.py`, `backend/tests/__init__.py`, and `backend/tests/conftest.py` (shared fixtures, `COBC_PATH` resolution via `os.environ.get("COBC_PATH", "cobc")`, module-scoped compiled-preset fixture)
    - Pin exact versions of `fastapi`, `uvicorn`, `pydantic`, `hypothesis`, `pytest`, `openai`, `httpx` in `backend/requirements.txt`; exclude `pandas`, `numpy`, `scipy`, `torch`
    - Write `backend/Dockerfile` from `python:3.12-slim`, install the `gnucobol` package, install requirements, and start Uvicorn on `${PORT:-8000}`
    - Add `backend/pytest.ini` so `pytest` runs in the container from `backend/`
    - Use `pathlib.Path` for every path
    - _Requirements: 1.3, 2.1, 2.2, 2.3, 2.4, 2.5, 2.7_

- [x] 2. Create COBOL presets and catalog
  - [x] 2.1 Write `backend/cobol_samples/loan_calc.cbl`
    - Free-format, Batch_Protocol: `ACCEPT` rows from SYSIN until EOF, one `DISPLAY` line per row, empty STDIN produces no output and exit 0
    - Input row `principal annual_rate_pct term_months`; output row `monthly_payment total_interest total_payable`
    - Explicit `PIC` scales (`9(9)V99`, `9(3)V999`, `9(3)`), no `COMP-1`/`COMP-2`
    - `COMPUTE ... ROUNDED` only at output stores, zero-rate branch is `principal / term_months`, `total_payable = monthly_payment * term_months`, `total_interest = total_payable - principal`
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

  - [x] 2.2 Write `backend/cobol_samples/compound_int.cbl`
    - Input row `principal annual_rate_pct compounds_per_year years`; output row `maturity_amount accrued_interest`
    - Explicit `PERFORM` loop over `compounds_per_year * years` periods, running balance stored `ROUNDED` in a `9(9)V99` field each period
    - `accrued_interest = maturity_amount - principal`; zero rate yields `principal`
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_

  - [x] 2.3 Write `backend/cobol_samples/daily_ledger.cbl`
    - Input row `opening_balance deposits withdrawals overdraft_threshold overdraft_fee`; output row `closing_balance fee_applied overdraft_flag`
    - Signed fields (`S9(9)V99`) for balances, threshold, and intermediate; no `ROUNDED` (truncation)
    - Fee and flag set when `opening + deposits - withdrawals < overdraft_threshold`; `closing = opening + deposits - withdrawals - fee_applied`
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8_

  - [x] 2.4 Write `backend/cobol_samples/presets.json`
    - One entry per preset keyed by `preset_id` with `name`, `description`, `source_file`, `input_schema` (`name`, `label`, `min`, `max`, `step`), and `sample_input`
    - Every Money_Value is a JSON string; `daily_ledger` fields in order `opening_balance, deposits, withdrawals, overdraft_threshold, overdraft_fee`, with `"default": "0.00"` on `overdraft_threshold` and it included in `sample_input`
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.8, 7.9_

  - [x]* 2.5 Write unit tests for catalog shape
    - In `backend/tests/test_catalog.py`: three entries, required keys, string-typed money values, sample inputs within `min`/`max`, `daily_ledger` field order and default
    - Verify `.gitattributes` rules and that `requirements.txt` excludes banned libraries
    - _Requirements: 1.2, 2.4, 7.1, 7.2, 7.3, 7.4, 7.7, 7.8, 7.9_

- [x] 3. Checkpoint - Presets and setup
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Implement reference models
  - [x] 4.1 Create `backend/app/reference_models.py` with the Decimal reference functions
    - Define `MONEY_CONTEXT = Context(prec=38)` and run every operation under `MONEY_CONTEXT.localcontext()`
    - Implement `loan_calc`, `compound_int`, `daily_ledger` (with `overdraft_threshold` default `Decimal("0.00")`), `REGISTRY`
    - Quantize only at COBOL store points: `ROUND_HALF_UP` where COBOL uses `ROUNDED`, `ROUND_DOWN` otherwise; compound_int rounds each period
    - Model receiving-field overflow by truncating high-order digits to the PIC capacity (no `ON SIZE ERROR`)
    - No `float`, `float()`, `math.pow`, or float-operand `**`
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7_

  - [x] 4.2 Add Float_Bug_Demo variants to `reference_models.py`
    - Implement `loan_calc_float_bug`, `compound_int_float_bug`, `daily_ledger_float_bug` and `FLOAT_BUG_REGISTRY`, returning `Decimal` results built from float arithmetic
    - Keep them isolated so the rest of the module stays float-free
    - _Requirements: 10.8_

  - [x]* 4.3 Write AST float-scan unit test
    - In `backend/tests/test_no_float.py`: parse `reference_models.py` with `ast`, assert no `float` literal, `float()` call, or `math.pow` outside `*_float_bug` functions
    - _Requirements: 10.5, 19.8_

- [x] 5. Implement Security_Validator and Cobol_Runner
  - [x] 5.1 Create `backend/app/cobol_runner.py` with `validate_source`
    - Define `ValidationError` and `ValidationResult` dataclasses
    - Strip comment lines, join continuation lines, match the ordered case-insensitive regex table, and report the token and original line number
    - Reject `CALL`, `SYSTEM`, `OPEN OUTPUT/EXTEND/I-O`, `SELECT...ASSIGN`, `READ`, `WRITE`, `REWRITE`, `DELETE`, `COPY`, `REPLACE`, `ACCEPT...FROM ENVIRONMENT`, `ACCEPT...FROM COMMAND-LINE`, `DISPLAY...UPON ENVIRONMENT`, `ENVIRONMENT-NAME`, `ENVIRONMENT-VALUE`, `COMMAND-LINE`, `CBL_` routines; reject sources over 64 KB
    - Accept `ACCEPT` without `FROM` or `FROM SYSIN`, `DISPLAY` without `UPON` or `UPON SYSOUT`, and the bare `ENVIRONMENT DIVISION` header
    - Must accept all three presets; never invoke a subprocess
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.9, 8.10, 8.11, 8.12, 8.13_

  - [x] 5.2 Add `CobolRunner` to `cobol_runner.py`
    - `compile_source`: write `program.cbl` into a sandbox dir and run `[cobc_path, "-x", "-free", "-o", "program", "program.cbl"]` with a 5 s timeout, argument list, no `shell=True`; `cobc_path` from `COBC_PATH`
    - `run_batch`: one execution with all rows on STDIN, 5 s timeout, truncate STDOUT at 1 MB and set `truncated`, kill on timeout and set `timed_out`
    - `compile_and_run_preset`: process-level cached binary per preset, compiled lazily once
    - `compile_and_run_custom`: validate first (no `cobc` call on rejection), fresh `tempfile.TemporaryDirectory`, compile, run only if compile succeeded, cleanup in `finally`
    - Return raw STDOUT, STDERR, exit code, and compile output
    - _Requirements: 8.8, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8, 9.9, 9.10_

  - [x]* 5.3 Write property test for Security_Validator
    - **Property 9: Security validator rejects every prohibited construct regardless of case or line-splitting**
    - **Validates: Requirements 8.1, 8.2, 8.3, 8.4, 8.5, 8.9, 8.10, 8.11, 8.12**

  - [x]* 5.4 Write unit tests for validator and runner
    - Per-token rejection examples naming token and line, acceptance of three presets, `ENVIRONMENT DIVISION` acceptance, 64 KB boundary
    - Sandbox cleanup after success, compile failure, run failure, and timeout; mocked `subprocess.run` asserts list args and no `shell=True`; 1 MB truncation; empty STDIN; rejected source never invokes `cobc`
    - _Requirements: 3.4, 8.6, 8.7, 8.8, 8.13, 9.2, 9.3, 9.6, 9.7, 9.8, 9.9, 19.2, 19.5, 19.6, 19.10_

  - [x]* 5.5 Write property test for batch protocol
    - **Property 1: Batch protocol preserves row count and order**
    - **Validates: Requirements 3.3**

  - [x]* 5.6 Write property test for zero-rate loan payment
    - **Property 2: Zero-rate loan payment equals straight division**
    - **Validates: Requirements 4.3**

  - [x]* 5.7 Write property test for loan totals
    - **Property 3: Loan derived totals are internally consistent**
    - **Validates: Requirements 4.6**

  - [x]* 5.8 Write property test for compound accrued interest
    - **Property 4: Compound interest accrued interest is internally consistent**
    - **Validates: Requirements 5.5**

  - [x]* 5.9 Write property test for zero-rate compounding
    - **Property 5: Zero-rate compounding leaves principal unchanged**
    - **Validates: Requirements 5.6**

  - [x]* 5.10 Write property test for overdraft threshold comparison
    - **Property 6: Overdraft fee and flag follow the threshold comparison**
    - Include explicit examples for thresholds `0.00`, positive, negative at balance equal, one cent below, and one cent above
    - **Validates: Requirements 6.4, 6.5, 19.11**

  - [x]* 5.11 Write property test for closing balance
    - **Property 7: Closing balance is internally consistent**
    - **Validates: Requirements 6.6**

  - [x]* 5.12 Write property test for overflow parity
    - **Property 10: Reference_Model overflow behavior matches COBOL**
    - **Validates: Requirements 10.6**

- [x] 6. Checkpoint - Models, validator, and runner
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Implement PBT_Verifier
  - [x] 7.1 Create `backend/app/pbt_verifier.py` with `Case` and `generate_cases`
    - Generate 150 to 250 `Decimal` cases within catalog ranges using Hypothesis (`database=None`, never via `float`), derandomized when `seed` is supplied
    - Always include: `0.00`, `0.01`, half-cent `x.xx5`, zero rate, one-month term, max PIC values, near-max balances, rate `3.333` for loan/compound, and for `daily_ledger` net balance `0.00`/`-0.01` plus threshold-relative cases (equal, minus 0.01, plus 0.01) with positive, negative, and zero thresholds
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 11.8, 11.9_

  - [x] 7.2 Add Restricted_Namespace loader to `pbt_verifier.py`
    - AST pre-check rejecting `Import`, `ImportFrom`, calls to `eval`, `exec`, `open`, `__import__`, `compile`, and any dunder `Attribute`/`Name`
    - `exec` with globals limited to `Decimal`, `ROUND_HALF_UP`, `ROUND_DOWN`, a fixed `Context`, and `__builtins__ = {}`; per-case `try/except` and 5 s watchdog marking failures as `python_error`
    - _Requirements: 12.3, 12.4_

  - [x] 7.3 Add `run_verification`, `Counterexample`, and `ParityReport` to `pbt_verifier.py`
    - Format all cases into one Batch_Protocol payload and call `CobolRunner` exactly once (preset or custom path); run the target function per case
    - Parse COBOL rows into `Decimal` (no `float`); return a verification error on row-count mismatch
    - Pass a case only when every field `Penny_Delta == Decimal("0.00")`; record at most 5 counterexamples with string-valued inputs, outputs, differing field, `penny_delta`, and `likely_cause` from `{float_drift, rounded_vs_truncation, scale_mismatch, overflow, python_error, unknown}`
    - Compute `parity_score_pct` with `Decimal` quantized to 2 places, `verified`, `execution_ms`, and at most 20 `raw_cobol_sample_output` lines
    - Support `simulate_float_bug` via `FLOAT_BUG_REGISTRY`
    - _Requirements: 12.1, 12.2, 12.5, 12.6, 12.7, 12.8, 12.9, 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7, 13.8, 13.9_

  - [x]* 7.4 Write property test for deterministic generation
    - **Property 17: Hypothesis case generation is deterministic under a fixed seed**
    - **Validates: Requirements 11.8**

  - [x]* 7.5 Write property test for restricted namespace
    - **Property 11: Restricted namespace blocks every forbidden construct**
    - **Validates: Requirements 12.3**

  - [x]* 7.6 Write property test for case pass criterion
    - **Property 12: A Case passes if and only if every output field matches at penny precision**
    - **Validates: Requirements 12.6, 12.8**

  - [x]* 7.7 Write property test for float-bug divergence
    - **Property 13: The float-bug demonstration always diverges**
    - **Validates: Requirements 12.9**

  - [x]* 7.8 Write property test for report shape
    - **Property 14: Parity report shape is always well-formed**
    - **Validates: Requirements 13.1, 13.5, 13.6, 13.7, 13.9**

  - [x]* 7.9 Write property test for score arithmetic
    - **Property 15: Parity score and verified flag are correct arithmetic/logical functions of the case counts**
    - **Validates: Requirements 13.2, 13.3, 13.4**

  - [x]* 7.10 Write property test for JSON round trip
    - **Property 16: Parity report survives a Decimal-aware JSON round trip**
    - **Validates: Requirements 13.10, 19.9**

  - [x]* 7.11 Write unit tests for verifier behavior
    - 100% parity per preset over at least 150 cases, float-bug failures per preset, row-count-mismatch error, `python_code` exception handling, and a 200-case verification under 3 seconds
    - _Requirements: 12.4, 12.7, 12.8, 12.9, 12.10, 19.3, 19.4, 19.7_

- [x] 8. Checkpoint - Verifier
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Implement FastAPI endpoints
  - [x] 9.1 Create `backend/app/main.py` with app, CORS, body limit, and `/health`
    - CORS allow-list from `FRONTEND_URL`, defaulting to `http://localhost:3000`
    - Middleware rejecting `Content-Length` over 128 KB before reading the body
    - `GET /health` returning `status`, `cobc_available`, `cobc_version` from a once-per-process cached `cobc --version` call; HTTP 200 with `false` and `""` when `cobc` is missing; no LLM, no compile
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 15.1, 15.2, 15.3, 17.15_

  - [x] 9.2 Create `backend/app/catalog.py` and add `GET /api/presets`
    - Load `presets.json` and the `.cbl` sources once per process with `json.load(..., parse_float=Decimal)` and serve from memory
    - Return `preset_id`, `name`, `description`, COBOL source, input schema, and sample input, all Money_Values as strings
    - _Requirements: 7.5, 16.1, 16.2, 16.3, 16.4_

  - [x] 9.3 Add `POST /api/verify` to `main.py`
    - Pydantic request model with strict `Decimal` parsing (reject float-typed Money_Values), `num_cases` default 150 and range 1 to 250
    - 422 for neither/both of `preset_id` and `cobol_code`, `cobol_code` without `python_code` or field schema, and bad `num_cases`; 404 unknown preset; 400 validator rejection with token and line; 422 compile failure; 504 timeout
    - Target function selection: user `python_code`, else Reference_Model, else Float_Bug_Demo when `simulate_float_bug`
    - Run live COBOL comparison on every call (no result caching)
    - _Requirements: 17.1, 17.2, 17.3, 17.4, 17.5, 17.6, 17.7, 17.8, 17.9, 17.10, 17.11, 17.12, 17.13, 17.14_

  - [x] 9.4 Add `POST /api/calculate` to `main.py`
    - Accept Money_Values as JSON strings or numbers converted to `Decimal` with no `float`; validate against catalog `min`/`max`, 422 naming the field for missing (no default), non-numeric, or out-of-range; 404 unknown preset
    - Default `overdraft_threshold` to `Decimal("0.00")` for `daily_ledger`, for both the Reference_Model and the COBOL row
    - Run the Reference_Model and the cached preset binary, return results as strings plus `raw_cobol_output`, and HTTP 500 naming the differing field on any nonzero Penny_Delta
    - _Requirements: 18.1, 18.2, 18.3, 18.4, 18.5, 18.6, 18.7, 18.8_

  - [x]* 9.5 Write property test for verify request validation
    - **Property 18: /api/verify request validation matrix is a pure function of request shape**
    - **Validates: Requirements 17.6, 17.7, 17.8, 17.9, 17.10, 17.11, 17.13**

  - [x]* 9.6 Write property test for calculate validation and mismatch detection
    - **Property 19: /api/calculate validates fields and detects any COBOL/Reference_Model mismatch**
    - **Validates: Requirements 18.3, 18.4, 18.5, 18.7, 18.8**

  - [x]* 9.7 Write property test for omitted overdraft threshold
    - **Property 8: Omitted overdraft threshold behaves as zero**
    - **Validates: Requirements 6.8, 18.8**

  - [x]* 9.8 Write unit tests for endpoints
    - `/health` shape and single `cobc --version` call, missing-`cobc` case, CORS origin handling, `/api/presets` shape and read-once caching, `/api/verify` repeated identical calls both invoke the compiler or binary, 128 KB body limit, `/api/calculate` happy path per preset and under 1.5 s
    - _Requirements: 14.1, 14.3, 14.4, 15.2, 15.3, 16.1, 16.4, 17.14, 17.15, 18.1, 18.2, 18.6_

- [-] 10. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP.
- Each task references specific requirements for traceability.
- Checkpoints ensure incremental validation.
- Property tests validate universal correctness properties; unit tests validate specific examples and edge cases.
- Property tests that use the live COBOL binary share one compiled binary per test module and batch examples into few `run_batch` calls.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["2.1", "2.2", "2.3"] },
    { "id": 2, "tasks": ["2.4"] },
    { "id": 3, "tasks": ["2.5"] },
    { "id": 4, "tasks": ["3"] },
    { "id": 5, "tasks": ["4.1", "5.1"] },
    { "id": 6, "tasks": ["4.2", "5.2"] },
    { "id": 7, "tasks": ["4.3", "5.3", "5.4", "5.5", "5.6", "5.7", "5.8", "5.9", "5.10", "5.11", "5.12"] },
    { "id": 8, "tasks": ["6"] },
    { "id": 9, "tasks": ["7.1"] },
    { "id": 10, "tasks": ["7.2"] },
    { "id": 11, "tasks": ["7.3"] },
    { "id": 12, "tasks": ["7.4", "7.5", "7.6", "7.7", "7.8", "7.9", "7.10", "7.11"] },
    { "id": 13, "tasks": ["8"] },
    { "id": 14, "tasks": ["9.1"] },
    { "id": 15, "tasks": ["9.2"] },
    { "id": 16, "tasks": ["9.3"] },
    { "id": 17, "tasks": ["9.4"] },
    { "id": 18, "tasks": ["9.5", "9.6", "9.7", "9.8"] },
    { "id": 19, "tasks": ["10"] }
  ]
}
```
