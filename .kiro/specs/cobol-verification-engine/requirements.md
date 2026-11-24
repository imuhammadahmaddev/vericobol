# Requirements Document

## Introduction

The COBOL Verification Engine is the core backend of VeriCOBOL. It compiles real COBOL banking programs with GnuCOBOL, runs them in batch against generated test inputs, and compares every monetary output with a Python `decimal.Decimal` implementation using differential property-based testing (Hypothesis). The result is a penny-exact parity report. The backend ships as a Docker image (`python:3.12-slim`) and is exposed through a FastAPI service.

This spec covers: cross-platform and Docker setup, three COBOL preset programs with metadata, a sandboxed COBOL compiler and runner, the differential PBT verifier with reference models, and the initial FastAPI endpoints. LLM agents, the frontend, and the in-memory pipeline cache are out of scope.

Budgets that apply to all requirements:
- Verification of one program (150 to 250 cases) completes in under 3 seconds on 0.1 CPU.
- Backend resident memory stays under 200 MB (hard limit 512 MB on Render Free Tier).

## Glossary

- **Backend**: The Python 3.12 FastAPI application under `backend/app/`, running in the Docker container.
- **Docker_Image**: The container image defined by `backend/Dockerfile`, based on `python:3.12-slim`, containing `cobc` and the Python dependencies.
- **Cobc**: The GnuCOBOL compiler executable resolved from the `COBC_PATH` environment variable (default `cobc`).
- **Preset**: One of three bundled banking programs (`loan_calc`, `compound_int`, `daily_ledger`) stored in `backend/cobol_samples/`.
- **Preset_Catalog**: The file `backend/cobol_samples/presets.json` holding metadata for all Presets.
- **Batch_Protocol**: The COBOL I/O contract. The program reads one input row per STDIN line until end of file and writes exactly one result line to STDOUT per input row. Fields are separated by a single space (one row per line).
- **Security_Validator**: The static checker in `backend/app/cobol_runner.py` that scans COBOL source before compilation.
- **Cobol_Runner**: The module `backend/app/cobol_runner.py` that validates, compiles once, and executes COBOL programs.
- **Sandbox_Directory**: A fresh `tempfile.TemporaryDirectory` used for one compile-and-run session.
- **PBT_Verifier**: The module `backend/app/pbt_verifier.py` that runs differential property-based testing.
- **Reference_Models**: The module `backend/app/reference_models.py` containing verified `Decimal` implementations of the three Presets.
- **Target_Function**: The Python function under test, either a Reference_Model or user-supplied `python_code`.
- **Restricted_Namespace**: The limited execution environment in which user-supplied `python_code` is loaded. It exposes `Decimal`, `decimal` rounding constants, and a fixed `decimal.Context`, and nothing else.
- **Case**: One set of input values for a Preset.
- **Parity_Report**: The structured JSON result of a verification run.
- **Counterexample**: A failing Case recorded with its inputs, COBOL output, Python output, differing field, and penny delta.
- **Penny_Delta**: `abs(cobol_value - python_value)` computed as `Decimal`. A Case passes only when every Penny_Delta equals `Decimal("0.00")`.
- **Float_Bug_Demo**: A deliberately naive Python implementation using IEEE 754 `float`, enabled by `simulate_float_bug=True`, used only to demonstrate that the PBT_Verifier detects discrepancies.
- **API**: The FastAPI service in `backend/app/main.py`.
- **Money_Value**: Any monetary amount, rate, fee, or balance.

## Requirements

### Requirement 1: Cross-Platform Line Endings

**User Story:** As a Windows developer, I want line endings normalized to LF, so that COBOL sources, Python files, and shell scripts behave identically on Windows, in the container, and on Render.

#### Acceptance Criteria

1. THE Repository SHALL contain a `.gitattributes` file at the repository root.
2. THE `.gitattributes` file SHALL set `eol=lf` for the patterns `*.cbl`, `*.py`, `*.sh`, and `Dockerfile`.
3. THE Backend SHALL construct every filesystem path with `pathlib.Path`.

### Requirement 2: Dockerized Runtime

**User Story:** As a developer, I want a single Docker image containing GnuCOBOL and the Python dependencies, so that compilation and tests run the same way on local Windows development and on Render Free Tier.

#### Acceptance Criteria

1. THE Docker_Image SHALL be built from the `python:3.12-slim` base image.
2. THE Docker_Image SHALL install the GnuCOBOL package so that `cobc` is available on the container path.
3. THE `backend/requirements.txt` file SHALL pin exact versions for `fastapi`, `uvicorn`, `pydantic`, `hypothesis`, `pytest`, `openai`, and `httpx`.
4. THE `backend/requirements.txt` file SHALL exclude `pandas`, `numpy`, `scipy`, `torch`, and any other heavy ML framework.
5. WHEN the container starts, THE Docker_Image SHALL launch the API with Uvicorn bound to the port given by the `PORT` environment variable, defaulting to 8000.
6. WHILE the API is idle after startup, THE Backend SHALL keep resident memory under 200 MB.
7. THE Docker_Image SHALL support running the `pytest` suite in `backend/tests/` inside the container.

### Requirement 3: Preset Program Batch Protocol

**User Story:** As a verification engineer, I want every preset COBOL program to process many input rows in one run, so that 150 to 250 cases execute in a single subprocess call.

#### Acceptance Criteria

1. THE Preset sources SHALL be GnuCOBOL free-format programs that compile with `cobc -x -free`.
2. THE Preset sources SHALL reside in `backend/cobol_samples/` as `loan_calc.cbl`, `compound_int.cbl`, and `daily_ledger.cbl`.
3. WHEN a Preset binary receives input rows on STDIN, THE Preset SHALL read rows until end of file and write exactly one result line to STDOUT per input row, in input order.
4. WHEN a Preset binary receives an empty STDIN, THE Preset SHALL write no result lines and exit with status 0.
5. THE Preset sources SHALL declare every Money_Value in WORKING-STORAGE with explicit fixed-point `PIC` clauses that include the scale.
6. THE Preset sources SHALL use no floating-point data items (`COMP-1`, `COMP-2`, `FLOAT-SHORT`, `FLOAT-LONG`).
7. WHEN a Preset binary processes 200 input rows, THE Preset SHALL complete execution in under 100 ms of wall-clock time on a 0.1 CPU allocation, excluding compilation.

### Requirement 4: Loan Amortization Preset

**User Story:** As a banking analyst, I want a loan calculator in COBOL, so that I can verify monthly payment, interest, and total payable.

#### Acceptance Criteria

1. THE `loan_calc.cbl` Preset SHALL accept the input fields `principal`, `annual_rate_pct`, and `term_months`.
2. THE `loan_calc.cbl` Preset SHALL output `monthly_payment`, `total_interest`, and `total_payable`.
3. WHEN `annual_rate_pct` equals 0, THE `loan_calc.cbl` Preset SHALL compute `monthly_payment` as `principal` divided by `term_months`.
4. WHEN a result is stored into an output field, THE `loan_calc.cbl` Preset SHALL apply `ROUNDED` at that store and use fixed-point intermediates without rounding earlier.
5. WHEN `term_months` equals 1, THE `loan_calc.cbl` Preset SHALL produce a `monthly_payment` equal to `principal` plus one month of interest.
6. THE `loan_calc.cbl` Preset SHALL derive `total_payable` as `monthly_payment` multiplied by `term_months`, and `total_interest` as `total_payable` minus `principal`.

### Requirement 5: Compound Interest Preset

**User Story:** As a banking analyst, I want a compound interest engine in COBOL, so that I can verify maturity amount and accrued interest.

#### Acceptance Criteria

1. THE `compound_int.cbl` Preset SHALL accept the input fields `principal`, `annual_rate_pct`, `compounds_per_year`, and `years`.
2. THE `compound_int.cbl` Preset SHALL output `maturity_amount` and `accrued_interest`.
3. THE `compound_int.cbl` Preset SHALL compute growth with an explicit `PERFORM` loop over `compounds_per_year` multiplied by `years` periods.
4. WHEN each compounding period completes, THE `compound_int.cbl` Preset SHALL store the running balance in a fixed-point field with a declared scale.
5. THE `compound_int.cbl` Preset SHALL derive `accrued_interest` as `maturity_amount` minus `principal`.
6. WHEN `annual_rate_pct` equals 0, THE `compound_int.cbl` Preset SHALL output `maturity_amount` equal to `principal`.

### Requirement 6: Daily Ledger Preset

**User Story:** As a banking analyst, I want a daily ledger reconciler in COBOL, so that I can verify closing balance and overdraft fee handling.

#### Acceptance Criteria

1. THE `daily_ledger.cbl` Preset SHALL accept the input fields `opening_balance`, `deposits`, `withdrawals`, `overdraft_threshold`, and `overdraft_fee`, in that order on each STDIN input row (Batch_Protocol).
2. THE `daily_ledger.cbl` Preset SHALL output `closing_balance`, `fee_applied`, and `overdraft_flag`.
3. THE `daily_ledger.cbl` Preset SHALL use COBOL default truncation (no `ROUNDED`) for every computed Money_Value.
4. IF `opening_balance` plus `deposits` minus `withdrawals` is less than `overdraft_threshold`, THEN THE `daily_ledger.cbl` Preset SHALL set `fee_applied` to `overdraft_fee` and `overdraft_flag` to 1.
5. IF `opening_balance` plus `deposits` minus `withdrawals` is greater than or equal to `overdraft_threshold`, THEN THE `daily_ledger.cbl` Preset SHALL set `fee_applied` to 0.00 and `overdraft_flag` to 0.
6. THE `daily_ledger.cbl` Preset SHALL compute `closing_balance` as `opening_balance` plus `deposits` minus `withdrawals` minus `fee_applied`.
7. THE `daily_ledger.cbl` Preset SHALL use signed fixed-point fields for `opening_balance`, `closing_balance`, `overdraft_threshold`, and the intermediate balance.
8. THE `overdraft_threshold` input SHALL default to 0.00, so that the overdraft rule is equivalent to charging `overdraft_fee` when the balance is less than 0.00.

### Requirement 7: Preset Catalog Metadata

**User Story:** As a frontend developer, I want structured metadata for each preset, so that I can render sliders, inputs, and sample values.

#### Acceptance Criteria

1. THE Preset_Catalog SHALL contain one entry for each of the three Presets, keyed by `preset_id` (`loan_calc`, `compound_int`, `daily_ledger`).
2. THE Preset_Catalog SHALL record for each entry a `name`, a `description`, an input schema, and a sample input.
3. THE Preset_Catalog SHALL record for each input field a `name`, `label`, `min`, `max`, and `step`.
4. THE Preset_Catalog SHALL store every Money_Value in `min`, `max`, `step`, and sample input as a JSON string.
5. THE Backend SHALL load the Preset_Catalog with a JSON parser configured to parse JSON numbers as `Decimal`.
6. WHEN the sample input of a Preset is run through the Cobol_Runner and the Reference_Model, THE Backend SHALL produce equal outputs.
7. THE sample input of every Preset SHALL fall within the `min` and `max` bounds of its input schema.
8. THE Preset_Catalog entry for `daily_ledger` SHALL define the input fields `opening_balance`, `deposits`, `withdrawals`, `overdraft_threshold`, and `overdraft_fee`, in that order.
9. THE Preset_Catalog entry for `daily_ledger` SHALL record a `default` of `"0.00"` for `overdraft_threshold` and SHALL include `overdraft_threshold` in the sample input.

### Requirement 8: COBOL Static Security Validation

**User Story:** As a platform operator, I want unsafe COBOL rejected before compilation, so that pasted code cannot reach the shell, filesystem, or environment.

#### Acceptance Criteria

1. WHEN COBOL source is submitted, THE Security_Validator SHALL scan the source before any compiler invocation.
2. IF the source contains `CALL`, `SYSTEM`, `OPEN OUTPUT`, `OPEN EXTEND`, `OPEN I-O`, `DELETE`, `ENVIRONMENT-NAME`, `ENVIRONMENT-VALUE`, `COMMAND-LINE`, or a `CBL_` prefixed routine, THEN THE Security_Validator SHALL reject the source and name the offending token and line number.
3. IF the source contains `SELECT ... ASSIGN`, `READ`, `WRITE`, `REWRITE`, `COPY`, or `REPLACE` statements, THEN THE Security_Validator SHALL reject the source.
4. IF the source contains `ACCEPT ... FROM ENVIRONMENT` or `DISPLAY ... UPON ENVIRONMENT`, THEN THE Security_Validator SHALL reject the source.
5. THE Security_Validator SHALL match prohibited tokens case-insensitively, ignoring COBOL comment lines, and including tokens split across continuation lines.
6. IF the source exceeds 64 KB, THEN THE Security_Validator SHALL reject the source.
7. THE Security_Validator SHALL accept all three Presets.
8. WHEN the Security_Validator rejects a source, THE Cobol_Runner SHALL not invoke Cobc.
9. WHEN the source contains an `ACCEPT` statement that reads from SYSIN or STDIN (including `ACCEPT identifier` without a `FROM` clause and `ACCEPT identifier FROM SYSIN`), THE Security_Validator SHALL accept that statement.
10. WHEN the source contains a `DISPLAY` statement that writes to STDOUT (including `DISPLAY` without an `UPON` clause and `DISPLAY ... UPON SYSOUT`), THE Security_Validator SHALL accept that statement.
11. IF the source contains `ACCEPT ... FROM ENVIRONMENT` or `ACCEPT ... FROM COMMAND-LINE`, THEN THE Security_Validator SHALL reject the source and name the offending token and line number.
12. IF the source contains `DISPLAY ... UPON ENVIRONMENT`, THEN THE Security_Validator SHALL reject the source and name the offending token and line number.
13. WHEN the source contains the `ENVIRONMENT DIVISION` header, THE Security_Validator SHALL accept that header, and THE Security_Validator SHALL apply the rejection rules of criteria 2, 4, 11, and 12 only to the `ENVIRONMENT` usages they name.

### Requirement 9: Safe Compile-Once Execution

**User Story:** As a verification engineer, I want one isolated compile and one batched run per verification, so that the time and memory budgets hold.

#### Acceptance Criteria

1. WHEN validated COBOL source is submitted, THE Cobol_Runner SHALL write it into a new Sandbox_Directory and compile it once with `cobc -x -free`, using the executable from `COBC_PATH`.
2. THE Cobol_Runner SHALL invoke subprocesses with argument lists and without `shell=True`.
3. THE Cobol_Runner SHALL enforce a 5-second timeout on each compile subprocess and each run subprocess.
4. WHEN the compile succeeds, THE Cobol_Runner SHALL execute the resulting binary once with all input rows supplied on STDIN.
5. THE Cobol_Runner SHALL return the raw STDOUT, STDERR, exit code, and compile output for display in the 3270 pane.
6. IF a subprocess exceeds its 5-second timeout, THEN THE Cobol_Runner SHALL terminate the process and return a timeout error.
7. IF compilation fails, THEN THE Cobol_Runner SHALL return the compiler diagnostics and skip execution.
8. IF STDOUT exceeds 1 MB, THEN THE Cobol_Runner SHALL truncate the captured output and report the truncation.
9. WHEN a session ends by success, failure, timeout, or exception, THE Cobol_Runner SHALL delete the Sandbox_Directory.
10. WHEN a Preset has been compiled, THE Cobol_Runner SHALL complete the compile in under 1.5 seconds on a 0.1 CPU allocation.

### Requirement 10: Reference Models

**User Story:** As a verification engineer, I want verified Decimal reference implementations of the presets, so that parity tests have a trusted Python baseline.

#### Acceptance Criteria

1. THE Reference_Models SHALL provide one pure `Decimal` function for each Preset.
2. THE Reference_Models SHALL use an explicit `decimal.Context` with sufficient precision and SHALL not use the ambient context.
3. THE Reference_Models SHALL quantize each result to the scale of the matching COBOL `PIC` clause at the same point where COBOL stores into the receiving field.
4. THE Reference_Models SHALL apply `ROUND_HALF_UP` where the COBOL source uses `ROUNDED` and `ROUND_DOWN` where it does not.
5. THE Reference_Models SHALL contain no `float` values, `float()` calls, `math.pow` calls, or `**` with a float operand.
6. WHEN an input exceeds the digit capacity of the receiving COBOL field, THE Reference_Models SHALL model the same overflow behavior as the COBOL program.
7. THE Reference_Models `daily_ledger` function SHALL accept `overdraft_threshold` (default `Decimal("0.00")`) and SHALL charge `overdraft_fee` and set `overdraft_flag` to 1 exactly when `opening_balance + deposits - withdrawals` is less than `overdraft_threshold`.
8. WHERE `simulate_float_bug=True` is selected, THE Reference_Models SHALL provide a Float_Bug_Demo implementation for each Preset that converts inputs to `float` and computes with floating-point arithmetic.

### Requirement 11: Adversarial Case Generation

**User Story:** As a verification engineer, I want Hypothesis to generate adversarial financial cases, so that rounding and boundary bugs surface.

#### Acceptance Criteria

1. WHEN a verification run starts, THE PBT_Verifier SHALL generate between 150 and 250 Cases, defaulting to 150.
2. THE PBT_Verifier SHALL generate Money_Values as `Decimal` values within the field ranges in the Preset_Catalog.
3. THE PBT_Verifier SHALL include in every run the Cases with a `0.00` amount, a `0.01` amount, a half-cent boundary (`x.xx5`) rate or amount, a zero rate, a one-month term, and a maximum PIC value.
4. WHERE the Preset is `daily_ledger`, THE PBT_Verifier SHALL include Cases where `opening_balance + deposits - withdrawals` equals `0.00` and equals `-0.01`, with `overdraft_threshold` at its default of 0.00.
5. WHERE the Preset is `daily_ledger`, THE PBT_Verifier SHALL include Cases with a non-default `overdraft_threshold` (positive, negative, and `0.00`) where `opening_balance + deposits - withdrawals` equals `overdraft_threshold`, equals `overdraft_threshold - 0.01`, and equals `overdraft_threshold + 0.01`.
6. WHERE the Preset is `loan_calc` or `compound_int`, THE PBT_Verifier SHALL include Cases with a repeating-decimal rate of `3.333`.
7. THE PBT_Verifier SHALL include Cases with large balances near the maximum of the input schema.
8. THE PBT_Verifier SHALL seed Hypothesis with a fixed derandomized seed when a `seed` parameter is supplied, so that identical seeds produce identical Cases.
9. THE PBT_Verifier SHALL not write the Hypothesis example database to disk.

### Requirement 12: Differential Execution and Comparison

**User Story:** As a compliance reviewer, I want every COBOL output compared with the Python output at penny precision, so that any discrepancy is detected.

#### Acceptance Criteria

1. WHEN Cases are generated, THE PBT_Verifier SHALL run all Cases against the compiled COBOL binary in one batched call.
2. THE PBT_Verifier SHALL run all Cases against the Target_Function in the same verification run.
3. WHERE user-supplied `python_code` is the Target_Function, THE PBT_Verifier SHALL load it in the Restricted_Namespace with no `import`, `open`, `eval`, `exec`, `__import__`, or attribute access to dunder names.
4. IF user-supplied `python_code` raises an exception or exceeds 5 seconds, THEN THE PBT_Verifier SHALL mark the affected Cases as failed and report the error text.
5. THE PBT_Verifier SHALL parse COBOL output fields into `Decimal` without using `float`.
6. THE PBT_Verifier SHALL mark a Case as passed only when every output field has a Penny_Delta of `Decimal("0.00")`.
7. IF the COBOL output line count differs from the Case count, THEN THE PBT_Verifier SHALL return a verification error identifying the count mismatch.
8. WHEN the Target_Function is the Reference_Model for a Preset, THE PBT_Verifier SHALL report 100% parity.
9. WHERE `simulate_float_bug=True`, THE PBT_Verifier SHALL use the Float_Bug_Demo as the Target_Function and report at least one failed Case across a default 150-Case run for each Preset.
10. WHEN a verification run executes 150 to 250 Cases, THE PBT_Verifier SHALL finish in under 3 seconds on a 0.1 CPU allocation, including one compile and one COBOL run.
11. WHILE a verification run executes, THE Backend SHALL keep resident memory under 200 MB.

### Requirement 13: Parity Report

**User Story:** As a dashboard user, I want a structured parity report, so that I can see the proof and any failing cases.

#### Acceptance Criteria

1. THE PBT_Verifier SHALL return a Parity_Report containing `total_cases`, `passed_cases`, `failed_cases`, `parity_score_pct`, `verified`, `execution_ms`, `raw_cobol_sample_output`, and `counterexamples`.
2. THE PBT_Verifier SHALL compute `parity_score_pct` as `passed_cases` divided by `total_cases` multiplied by 100, computed with `Decimal` and quantized to two decimal places.
3. THE PBT_Verifier SHALL set `verified` to true only when `failed_cases` equals 0 and `total_cases` is greater than 0.
4. THE PBT_Verifier SHALL set `total_cases` equal to `passed_cases` plus `failed_cases`.
5. THE PBT_Verifier SHALL include in `counterexamples` at most 5 entries.
6. THE PBT_Verifier SHALL record in each Counterexample the exact input values, COBOL output, Python output, differing field name, and Penny_Delta, all Money_Values serialized as strings.
7. THE PBT_Verifier SHALL record in each Counterexample a likely-cause label chosen from `float_drift`, `rounded_vs_truncation`, `scale_mismatch`, `overflow`, `python_error`, or `unknown`.
8. THE PBT_Verifier SHALL set `execution_ms` to the measured wall-clock time of the verification run in milliseconds.
9. THE PBT_Verifier SHALL limit `raw_cobol_sample_output` to the first 20 COBOL output lines.
10. FOR ALL Parity_Report objects, serializing to JSON and parsing back with `parse_float=Decimal` SHALL produce an equivalent object (round-trip property).

### Requirement 14: Health Endpoint

**User Story:** As a frontend developer, I want a cheap health endpoint, so that the UI can warm up the Render cold start without side effects.

#### Acceptance Criteria

1. WHEN a client sends `GET /health`, THE API SHALL return HTTP 200 with a JSON body containing `status` equal to `"ok"`, `cobc_available` as a boolean, and `cobc_version` as a string.
2. WHEN a client sends `GET /health`, THE API SHALL not invoke any LLM and SHALL not compile any user-supplied COBOL.
3. THE API SHALL determine `cobc_available` and `cobc_version` by running `cobc --version` at most once per process and caching the result.
4. IF Cobc is not found, THEN THE API SHALL return `status` equal to `"ok"`, `cobc_available` equal to false, and `cobc_version` equal to an empty string.
5. WHEN `GET /health` is called after the first call, THE API SHALL respond in under 50 ms.

### Requirement 15: CORS Configuration

**User Story:** As a frontend developer, I want the API to accept requests only from the configured frontend, so that the deployment is not open to arbitrary origins.

#### Acceptance Criteria

1. THE API SHALL read the allowed origin from the `FRONTEND_URL` environment variable.
2. THE API SHALL permit cross-origin requests only from the origin given by `FRONTEND_URL`.
3. IF `FRONTEND_URL` is unset, THEN THE API SHALL permit only `http://localhost:3000`.

### Requirement 16: Presets Endpoint

**User Story:** As a frontend developer, I want one call that returns all presets with their source and input metadata, so that the UI can render the legacy pane and sliders.

#### Acceptance Criteria

1. WHEN a client sends `GET /api/presets`, THE API SHALL return HTTP 200 with all three Presets.
2. THE API SHALL include in each Preset entry the `preset_id`, `name`, `description`, COBOL source text, input schema with `min`, `max`, and `step`, and sample input.
3. THE API SHALL serialize every Money_Value in the response as a JSON string.
4. THE API SHALL read Preset files from disk at most once per process and serve later requests from memory.

### Requirement 17: Verify Endpoint

**User Story:** As a user, I want to submit a preset or custom COBOL and Python code and receive a live parity proof, so that I can trust the modernized code.

#### Acceptance Criteria

1. WHEN a client sends `POST /api/verify` with a valid `preset_id`, THE API SHALL run the differential PBT against that Preset's COBOL source and return a Parity_Report.
2. WHEN a client sends `POST /api/verify` with `cobol_code`, THE API SHALL pass the source through the Security_Validator before compilation.
3. WHEN `python_code` is supplied, THE API SHALL use it as the Target_Function.
4. WHEN `python_code` is omitted and `preset_id` is supplied, THE API SHALL use the Reference_Model as the Target_Function.
5. WHERE `simulate_float_bug` is true, THE API SHALL use the Float_Bug_Demo as the Target_Function.
6. WHERE `num_cases` is omitted, THE API SHALL use 150 Cases.
7. IF `num_cases` is below 1 or above 250, THEN THE API SHALL return HTTP 422 with a validation error.
8. IF neither `preset_id` nor `cobol_code` is supplied, or both are supplied, THEN THE API SHALL return HTTP 422.
9. IF `preset_id` is unknown, THEN THE API SHALL return HTTP 404.
10. IF `cobol_code` is supplied without a `preset_id`, THEN THE API SHALL require `python_code` and the field schema, and SHALL return HTTP 422 when either is missing.
11. IF the Security_Validator rejects the source, THEN THE API SHALL return HTTP 400 with the rejected token and line number and SHALL not compile.
12. IF compilation fails or a subprocess times out, THEN THE API SHALL return HTTP 422 or HTTP 504 respectively with the compiler diagnostics or timeout message.
13. WHEN a request body is parsed, THE API SHALL parse JSON numbers as `Decimal` and reject float-typed Money_Values.
14. THE API SHALL execute the live GnuCOBOL comparison on every `POST /api/verify` call, including repeated calls with identical input.
15. THE API SHALL limit request body size to 128 KB.

### Requirement 18: Calculate Endpoint

**User Story:** As a dashboard user, I want to move sliders and get exact results from the verified model, so that the cloud dashboard reflects the legacy program's behavior.

#### Acceptance Criteria

1. WHEN a client sends `POST /api/calculate` with a valid `preset_id` and inputs, THE API SHALL return the result computed by the Reference_Model with each Money_Value serialized as a string.
2. WHEN the calculation completes, THE API SHALL run the same inputs through the compiled Preset binary and return the raw COBOL STDOUT in a `raw_cobol_output` field.
3. IF the COBOL output differs from the Reference_Model result by a Penny_Delta other than `Decimal("0.00")`, THEN THE API SHALL return HTTP 500 with an error identifying the differing field.
4. IF an input other than a field with a Preset_Catalog `default` is missing, non-numeric, or outside the `min` and `max` bounds of the Preset_Catalog, THEN THE API SHALL return HTTP 422 naming the offending field.
5. IF `preset_id` is unknown, THEN THE API SHALL return HTTP 404.
6. WHEN `POST /api/calculate` is called, THE API SHALL respond in under 1.5 seconds on a 0.1 CPU allocation.
7. THE API SHALL accept Money_Value inputs as JSON strings or JSON numbers and SHALL convert both to `Decimal` without an intermediate `float`.
8. WHEN `overdraft_threshold` is omitted from a `daily_ledger` request, THE API SHALL use `Decimal("0.00")` for the Reference_Model and for the COBOL input row.

### Requirement 19: Test Coverage

**User Story:** As a maintainer, I want automated tests inside the Docker container, so that regressions in parity, security, or performance are caught.

#### Acceptance Criteria

1. THE Repository SHALL include `pytest` tests in `backend/tests/` that run inside the Docker_Image.
2. THE test suite SHALL verify that each Preset compiles with `cobc -x -free` and satisfies the Batch_Protocol.
3. THE test suite SHALL verify that each Reference_Model achieves 100% parity against its Preset over at least 150 Cases.
4. THE test suite SHALL verify that the Float_Bug_Demo produces at least one failed Case for each Preset.
5. THE test suite SHALL verify that the Security_Validator rejects each prohibited token listed in Requirement 8 and accepts all three Presets.
6. THE test suite SHALL verify that the Sandbox_Directory is deleted after success, failure, and timeout.
7. THE test suite SHALL verify that a verification run of 200 Cases finishes in under 3 seconds.
8. THE test suite SHALL scan `backend/app/` and confirm that no `float` usage appears in Money_Value computations of `reference_models.py`, excluding the Float_Bug_Demo.
9. THE test suite SHALL verify the JSON round-trip property of Requirement 13 criterion 10.
10. THE test suite SHALL verify that the Security_Validator accepts `ACCEPT` from SYSIN and `DISPLAY` to STDOUT and the `ENVIRONMENT DIVISION` header, and rejects `ACCEPT ... FROM ENVIRONMENT`, `ACCEPT ... FROM COMMAND-LINE`, `DISPLAY ... UPON ENVIRONMENT`, `ENVIRONMENT-NAME`, and `ENVIRONMENT-VALUE`, naming the token and line number.
11. THE test suite SHALL verify the `daily_ledger` overdraft rule at `overdraft_threshold` values of `0.00`, a positive value, and a negative value, at balances equal to, one cent below, and one cent above the threshold, and that omitting the threshold behaves as `0.00`.
