# Requirements Document

## Introduction

The Universal_Platform removes all hardcoded business logic and pre-baked conversion results from VeriCOBOL. A judge can bring any COBOL program (paste, edit, or upload `.cbl`, `.cob`, `.txt`), and the program passes through the same live three-agent pipeline: Legacy_Parser infers the contract and I/O protocol, Cloud_Architect generates exact-Decimal Python, and Parity_Auditor proves penny-exact parity against real GnuCOBOL with live self-healing. The three sample programs are optional starter templates and receive no special treatment. The feature covers the backend (LLM client, COBOL runner, pipeline, endpoints), the frontend (full editor control, dynamic dashboard, dark fintech theme), and the README, tests, Docker build, and git delivery.

Decisions that bound this feature:
- WHEN the LLM is unavailable, the API returns HTTP 503 with a clear message. The API never falls back to `preset_bundles.json`.
- `backend/cobol_samples/preset_bundles.json` and `backend/app/reference_models.py` are removed from the runtime (pipeline, endpoints, cache). The files stay in the repository as test-only oracles.
- Optional property-test sub-tasks are skipped in the task plan.
- The file `.env` is never read, printed, or staged by any task. The `.env` file must be git-ignored, and `.env.example` holds placeholders only.

## Glossary

- **Universal_Platform**: The VeriCOBOL backend and frontend after this feature.
- **Backend**: The FastAPI application under `backend/app/`.
- **Frontend**: The Next.js application under `frontend/`.
- **Pipeline**: The runtime sequence Legacy_Parser, Cloud_Architect, Parity_Auditor.
- **Legacy_Parser**: Stage 1, a live LLM call that infers program metadata, IO_Protocol, Contract, business rules and dashboard metadata from raw COBOL.
- **Cloud_Architect**: Stage 2, a live LLM call that generates exact-Decimal Python.
- **Parity_Auditor**: Stage 3, which runs live differential property-based testing against GnuCOBOL and drives self-healing.
- **Verification_Engine**: The component in `backend/app/pbt_verifier.py` that compiles the COBOL once, runs cases against the binary, and compares outputs with the generated Python.
- **LLM_Client**: The OpenAI-compatible client in `backend/app/llm_client.py`, configured only through `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL`.
- **Budget_Tracker**: The component inside LLM_Client that accumulates LLM spend.
- **Cobol_Runner**: The `CobolRunner` component in `backend/app/cobol_runner.py` that validates, compiles and executes COBOL.
- **Sandbox_Validator**: The `validate_source()` function that statically checks COBOL source before any compile or LLM call.
- **Binary_Cache**: The bounded LRU cache of compiled binaries in Cobol_Runner.
- **Pipeline_Cache**: The in-memory cache of pipeline results, keyed on SHA-256.
- **Normalized_Source**: COBOL source after normalization of line endings, trailing whitespace and comment lines.
- **IO_Protocol**: One of `batch_space`, `single_run_lines`, `single_run_space`, `no_input_static`.
- **batch_space**: IO_Protocol where the program loops reading one space-separated input line per case until EOF and writes one output line per case.
- **single_run_lines**: IO_Protocol where the program issues one ACCEPT per input field on separate lines, prints the output, then executes STOP RUN.
- **single_run_space**: IO_Protocol where the program reads one space-separated input line, prints the output, then executes STOP RUN.
- **no_input_static**: IO_Protocol where the program has zero ACCEPT statements and computes a deterministic output from WORKING-STORAGE.
- **Contract_Schema**: The inferred description of inputs (name, label, pic, scale, min, max, step) and outputs (name, label, pic, scale, rounding, format).
- **Output_Format**: One of `currency`, `percent`, `integer`, `decimal`, `flag`, `text`.
- **Chart_Type**: One of `bar_comparison`, `breakdown`, `waterfall`, `summary`.
- **Dashboard_Metadata**: Object with title, chart_type, inputs and outputs, used by the Frontend to render the dashboard.
- **Counterexample**: The exact failing case (inputs, COBOL output, Python output, differing field, likely cause).
- **Float_Demo_Attempt**: Attempt 1 of Self_Healing_Demo, in which Cloud_Architect produces a naive IEEE-754 float translation executed with `allow_float_demo=True`.
- **Self_Healing_Demo**: Pipeline mode enabled by `demonstrate_self_healing=true`.
- **Self_Healing_Loop**: Regeneration of Python from a Counterexample, at most 2 retries in the normal mode.
- **Parity_Report**: The Verification_Engine result with passed and total counts, parity score and Counterexample list.
- **Certified**: A Parity_Report in which every case matches with a difference of 0.
- **Resurrect_Response**: The JSON body returned by `POST /api/resurrect`.
- **Starter_Template**: One of the three `.cbl` files (`loan_calc.cbl`, `compound_int.cbl`, `daily_ledger.cbl`) in `backend/cobol_samples/`.
- **Retired_Artifacts**: `backend/cobol_samples/preset_bundles.json` and `backend/app/reference_models.py`.
- **Test_Oracle**: Use of a Retired_Artifact only from files under `backend/tests/`.
- **LLM_Unavailable**: The state in which `LLM_API_KEY` is unset, empty or a placeholder, an LLM call fails after its retry policy, or the budget is exhausted.
- **Legacy_Panel**: The left-pane component containing CobolSourceEditor, the 3270 terminal and its toolbar, styled `#0A0E0C` with `#00FF66` text.
- **Cloud_Dashboard**: The `CloudDashboard.tsx` component in the right pane.
- **Dynamic_Chart**: The `DynamicChart.tsx` native SVG component.
- **Dark_Fintech_Theme**: Containers `bg-[#0F172A]`, inner cards `bg-[#1E293B]`, borders `border-slate-800` or `border-slate-700`, text `text-slate-100` or `text-slate-400`.
- **Themed_Components**: PipelineStepper, CloudDashboard, ExportTab, VerifiedPythonTab, AuditTrail and DynamicChart.
- **Health_Pill**: The Frontend component that shows compiler status.
- **Judge**: A person using the public web UI.

## Requirements

### Requirement 1: LLM client configuration and budget

**User Story:** As an operator, I want a budget-capped, env-configured LLM client, so that the total LLM spend stays within $2 and no secret is hardcoded.

#### Acceptance Criteria

1. THE `.env.example` file SHALL set `LLM_MODEL=gpt-6-luna` and SHALL contain only placeholder values for `LLM_BASE_URL`, `LLM_API_KEY` and every other secret.
2. THE LLM_Client SHALL read its endpoint, key and model only from `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL`.
3. THE Budget_Tracker SHALL price input tokens at `Decimal("0.10")` per 1 million tokens and output tokens at `Decimal("0.50")` per 1 million tokens.
4. THE Budget_Tracker SHALL use a spend cap of `Decimal("2.00")` USD, overridden by the `LLM_BUDGET_USD` environment variable when set.
5. WHEN cumulative spend reaches the cap, THE LLM_Client SHALL stop issuing calls and report the LLM_Unavailable state with cause "budget exhausted".
6. WHEN the LLM_Client calls the OpenAI-compatible `/v1/chat/completions` endpoint, THE LLM_Client SHALL strip surrounding markdown code fences, including ```` ```json ````, from the reply before parsing.
7. WHEN the LLM_Client parses a reply, THE LLM_Client SHALL call `json.loads` with `parse_float=Decimal`.
8. THE Universal_Platform SHALL omit API keys from logs, responses and error messages.

### Requirement 2: COBOL compilation in free and fixed format

**User Story:** As a judge, I want programs in either source format to compile, so that classic 80-column COBOL works.

#### Acceptance Criteria

1. WHEN Cobol_Runner compiles a source, THE Cobol_Runner SHALL first run `cobc -x -free`.
2. IF the `-free` compile fails with a syntax error, THEN THE Cobol_Runner SHALL retry once with fixed-format `cobc -x`.
3. WHEN a compile succeeds, THE Cobol_Runner SHALL return the format that succeeded as `"free"` or `"fixed"`.
4. THE Cobol_Runner SHALL resolve the compiler from `COBC_PATH`, defaulting to `cobc`.
5. THE Cobol_Runner SHALL run each compile and each run with a 5-second timeout, an argument list, and a fresh temporary directory removed in all outcomes.
6. IF both compile attempts fail, THEN THE Universal_Platform SHALL return a client error that includes the compiler diagnostics from the last attempt.

### Requirement 3: Strict sandbox validation

**User Story:** As a security reviewer, I want hostile COBOL rejected before any compile or LLM call, so that untrusted input cannot reach the system.

#### Acceptance Criteria

1. WHEN any endpoint receives COBOL source, THE Sandbox_Validator SHALL validate the source before any compile or LLM call.
2. IF the source contains `CALL`, `SYSTEM`, file I/O (`SELECT ... ASSIGN`, `OPEN`, `READ`, `WRITE`, `REWRITE`, `DELETE`), `COPY`, `REPLACE`, or environment or command-line access (`ACCEPT ... FROM ENVIRONMENT`, `DISPLAY ... UPON ENVIRONMENT`, `ACCEPT ... FROM COMMAND-LINE`), THEN THE Sandbox_Validator SHALL reject the source and THE Universal_Platform SHALL return HTTP 400 naming the construct and its line number.
3. IF the Sandbox_Validator rejects a source, THEN THE Universal_Platform SHALL make 0 LLM calls and 0 compiler calls for that request.
4. WHEN a fixed-format line has `*` in column 7, THE Sandbox_Validator SHALL treat the line as a comment and SHALL NOT report a prohibited token found in it.
5. WHEN an executable line contains a prohibited token, THE Sandbox_Validator SHALL reject the source, including when the source is fixed format.
6. IF the source exceeds the configured size cap, THEN THE Universal_Platform SHALL return HTTP 413.
7. THE Universal_Platform SHALL cap stdout size from compiled programs and treat COBOL source, stdout and LLM output as untrusted data.

### Requirement 4: Compiled binary LRU cache

**User Story:** As a judge, I want slider changes on any custom program to respond instantly, so that the dashboard feels live.

#### Acceptance Criteria

1. THE Cobol_Runner SHALL keep a Binary_Cache with a maximum of 16 entries.
2. THE Binary_Cache SHALL key each entry on the SHA-256 of the `cobol_source` text.
3. WHEN the Binary_Cache holds 16 entries and a new binary is added, THE Binary_Cache SHALL evict the least recently used entry and delete its files.
4. WHEN a request runs a `cobol_source` that has a Binary_Cache entry, THE Cobol_Runner SHALL run the cached binary without recompiling.
5. WHILE the Binary_Cache holds the binary for a program, THE Cobol_Runner SHALL complete one single-input run of that program in under 50 ms.

### Requirement 5: Stage 1, Legacy_Parser with inference

**User Story:** As a judge, I want the platform to infer everything from raw COBOL, so that I do not write schemas.

#### Acceptance Criteria

1. WHEN the Legacy_Parser receives `cobol_code`, THE Legacy_Parser SHALL accept it without a caller-supplied `field_schema`.
2. THE Legacy_Parser SHALL infer `program_title`, `description` and `io_protocol` through a live LLM call.
3. THE Legacy_Parser SHALL assign `io_protocol` one of `batch_space`, `single_run_lines`, `single_run_space`, `no_input_static`.
4. THE Legacy_Parser SHALL infer a Contract_Schema in which each input has name, label, pic, scale, min, max and step, and each output has name, label, pic, scale, rounding and format.
5. THE Legacy_Parser SHALL assign each output a format of `currency`, `percent`, `integer`, `decimal`, `flag` or `text`.
6. THE Legacy_Parser SHALL infer plain-English `business_rules` from the PROCEDURE DIVISION.
7. THE Legacy_Parser SHALL infer `dashboard_metadata` with title, chart_type (`bar_comparison`, `breakdown`, `waterfall` or `summary`), inputs and outputs.
8. IF the LLM output is not valid JSON or violates the schema, THEN THE Legacy_Parser SHALL retry once with the validation error, and on a second failure THE Universal_Platform SHALL return HTTP 502 with the validation error.
9. IF the inferred `io_protocol` is `no_input_static`, THEN THE Legacy_Parser SHALL accept a Contract_Schema with zero inputs.
10. IF the inferred `io_protocol` is not `no_input_static` and the Contract_Schema has no inputs or no outputs, THEN THE Universal_Platform SHALL return HTTP 422 naming the missing part.

### Requirement 6: Stage 2, Cloud_Architect

**User Story:** As a bank engineer, I want generated Python that matches COBOL arithmetic exactly, so that no rounding hallucination reaches production.

#### Acceptance Criteria

1. WHEN the Cloud_Architect runs, THE Cloud_Architect SHALL generate standalone Python 3.12 source that defines `def calculate(**inputs) -> dict`.
2. THE Cloud_Architect SHALL generate source that uses `decimal.Decimal` with an explicit `decimal.Context`, matching the Contract_Schema scale and PIC capacity.
3. THE Cloud_Architect SHALL generate `ROUND_DOWN` for COBOL stores without `ROUNDED` and `ROUND_HALF_UP` for stores with `ROUNDED`.
4. IF generated source contains a `float` type, a float literal, `math.pow` or a float-operand `**`, THEN THE Universal_Platform SHALL reject that attempt and count it as a failed verification attempt, except in a Float_Demo_Attempt.
5. WHEN the Cloud_Architect has produced verified code, THE Universal_Platform SHALL render the standalone FastAPI service through `export_template.render()` and return it as `export_fastapi_code`.
6. THE Universal_Platform SHALL execute generated Python only in the restricted namespace of the Verification_Engine.

### Requirement 7: Stage 3, Parity_Auditor universal verification

**User Story:** As a compliance officer, I want live GnuCOBOL proof for every program regardless of I/O style, so that I can trust any result.

#### Acceptance Criteria

1. WHILE `io_protocol` is `batch_space`, THE Verification_Engine SHALL run 150 randomized adversarial Decimal cases in 1 batched GnuCOBOL call.
2. WHILE `io_protocol` is `single_run_lines` or `single_run_space`, THE Verification_Engine SHALL compile once and run between 35 and 50 randomized adversarial cases against the cached binary.
3. WHILE `io_protocol` is `single_run_lines` or `single_run_space`, THE Verification_Engine SHALL complete all cases in under 2.5 seconds on a 0.1 CPU allocation.
4. WHILE `io_protocol` is `single_run_lines` or `single_run_space`, THE Verification_Engine SHALL include min and max bounds, 0.00, 0.01, negative values for signed `S9` fields, and half-cent decimals among the cases.
5. WHILE `io_protocol` is `no_input_static`, THE Verification_Engine SHALL compile once, run once, and require exact output parity with `calculate()`.
6. THE Verification_Engine SHALL compare every output field of the generated Python with the COBOL output and require a difference of 0.
7. THE Parity_Auditor SHALL run the Verification_Engine against live GnuCOBOL on every request, including Pipeline_Cache hits.
8. THE Parity_Auditor stage in the Resurrect_Response SHALL report `source` equal to `"live"` and SHALL NOT report `"cache"`.
9. THE Verification_Engine SHALL compile each program once per verification run and SHALL NOT recompile inside the case loop.
10. WHILE `io_protocol` is `batch_space`, THE Verification_Engine SHALL complete 150 cases in under 3 seconds on a 0.1 CPU allocation.

### Requirement 8: Live self-healing on any program

**User Story:** As a judge, I want to watch a naive float translation fail and heal on my own program, so that the proof is concrete.

#### Acceptance Criteria

1. IF the Parity_Report contains a mismatch, THEN THE Parity_Auditor SHALL send the first Counterexample to the Cloud_Architect and request corrected Python.
2. THE Self_Healing_Loop SHALL perform at most 2 retries.
3. WHEN a verification attempt reaches Certified, THE Parity_Auditor SHALL stop the Self_Healing_Loop.
4. WHERE `demonstrate_self_healing` is true, THE Pipeline SHALL run on any program, with no program-specific code path.
5. WHERE `demonstrate_self_healing` is true, THE Cloud_Architect SHALL produce a naive IEEE-754 float translation as attempt 1, and THE Verification_Engine SHALL run it with `allow_float_demo=True` in the restricted runner.
6. WHEN the Float_Demo_Attempt returns a float result, THE Verification_Engine SHALL convert it with `Decimal(str(x))` and measure the exact drift against the GnuCOBOL output.
7. IF the Float_Demo_Attempt shows any drift or mismatch, THEN THE Parity_Auditor SHALL feed the concrete Counterexample to the Cloud_Architect as attempt 2, request exact Decimal code, and re-verify with live GnuCOBOL.
8. IF the Parity_Report still contains a mismatch after the final retry, THEN THE Universal_Platform SHALL return HTTP 200 with the full audit trail, the last Parity_Report and `certified` set to false.
9. THE Resurrect_Response SHALL contain an audit trail with one record per stage execution and per verification attempt.

### Requirement 9: Live pipeline without bundle bypass

**User Story:** As a judge, I want every program, including starter templates, to go through the real LLM pipeline, so that nothing is pre-baked.

#### Acceptance Criteria

1. THE Universal_Platform SHALL remove every runtime import of `reference_models` and every runtime read of `preset_bundles.json` from `backend/app/`.
2. WHEN any program, including a Starter_Template, is submitted, THE Universal_Platform SHALL run it through the live Pipeline.
3. WHERE a test under `backend/tests/` uses a Retired_Artifact, THE test SHALL use it only as a Test_Oracle.
4. IF the Universal_Platform is in the LLM_Unavailable state when a request needs the LLM, THEN THE Universal_Platform SHALL return HTTP 503 with a message that states the LLM is unavailable and names the cause (missing key, provider failure or budget exhausted).
5. IF the Universal_Platform is in the LLM_Unavailable state, THEN THE Universal_Platform SHALL NOT read `preset_bundles.json` and SHALL NOT return pre-built results.

### Requirement 10: Pipeline cache

**User Story:** As an operator, I want repeated runs to cost no LLM tokens, so that the $2 budget lasts.

#### Acceptance Criteria

1. THE Pipeline_Cache SHALL be an in-memory runtime cache keyed on the SHA-256 of the Normalized_Source combined with the `demonstrate_self_healing` value.
2. THE Pipeline_Cache SHALL store only Certified results produced by the live Pipeline.
3. WHEN two sources differ only in whitespace, case of keywords or comment lines, THE Pipeline_Cache SHALL map them to the same key.
4. WHEN a request hits the Pipeline_Cache, THE Universal_Platform SHALL serve the cached stages without an LLM call and SHALL still run the live Verification_Engine.
5. WHERE `force_live_llm` is true, THE Universal_Platform SHALL bypass the Pipeline_Cache and run the live LLM stages.
6. WHEN the backend restarts, THE Pipeline_Cache SHALL start empty.
7. THE Backend SHALL keep resident memory under 200 MB as the target.

### Requirement 11: Resurrect endpoint

**User Story:** As a Judge, I want one endpoint that takes raw COBOL, so that the UI and API stay simple.

#### Acceptance Criteria

1. WHEN a caller sends `POST /api/resurrect` with `cobol_code`, `num_cases` (default 150), `demonstrate_self_healing` (default false) and `force_live_llm` (default false), THE Backend SHALL run the Pipeline and return the Resurrect_Response.
2. WHERE a caller supplies `preset_id` instead of `cobol_code`, THE Backend SHALL load that Starter_Template as raw text and run it through the same Pipeline.
3. THE Resurrect_Response SHALL include `cobol_code`, `program_title`, `description`, `io_protocol`, the Contract_Schema, `business_rules`, `dashboard_metadata`, `verified_python_code`, `export_fastapi_code`, the Parity_Report, the audit trail and `certified`.
4. IF `preset_id` is unknown, THEN THE Backend SHALL return HTTP 404.
5. THE Backend SHALL parse request numbers with `parse_float=Decimal` and accept amounts as strings.

### Requirement 12: Calculate-custom endpoint

**User Story:** As a Judge, I want sliders to run both Python and real COBOL on one input set, so that every KPI is proven live.

#### Acceptance Criteria

1. WHEN a caller sends `POST /api/calculate-custom` with `cobol_code`, `python_code`, `schema`, `io_protocol` and `inputs` (a map of string to string), THE Backend SHALL validate `cobol_code` with the Sandbox_Validator before any compile.
2. WHEN the request is valid, THE Backend SHALL run the restricted `python_code` and the compiled GnuCOBOL binary from the Binary_Cache on the single input set.
3. THE Backend SHALL compare every output field of the Python result with the GnuCOBOL result and require a difference of 0.
4. THE Backend SHALL return `result` (map of string to string), `raw_cobol_output` (string) and `parity_verified` (boolean).
5. IF an input violates its schema range or PIC capacity, THEN THE Backend SHALL return HTTP 422 naming the field.
6. IF the Sandbox_Validator rejects `cobol_code`, THEN THE Backend SHALL return HTTP 400.

### Requirement 13: Export-custom endpoint

**User Story:** As an engineer, I want to download verified code for any program, so that I can run it outside VeriCOBOL.

#### Acceptance Criteria

1. WHEN a caller sends `POST /api/export-custom` with `program_title`, `verified_python_code` and `schema`, THE Backend SHALL return `filename`, `verified_python_code`, `export_fastapi_code` and `certified`.
2. THE Backend SHALL build `export_fastapi_code` with `export_template.render()`.
3. THE exported module SHALL contain no `float` type, float literal or `math.pow`.

### Requirement 14: Starter templates and health

**User Story:** As a Judge, I want optional starter programs and a cheap health check, so that I can start quickly and detect cold start.

#### Acceptance Criteria

1. THE `GET /api/presets` endpoint SHALL return ids, titles and raw COBOL text of the three Starter_Templates, with no Contract, no pre-verified results and no Python.
2. WHEN a caller requests `GET /health`, THE Backend SHALL respond without invoking the LLM_Client or the compiler, except for reading a cached compiler version string.
3. THE `/health` response SHALL state whether the LLM is configured as a boolean, without exposing the key.

### Requirement 15: Judge control of the COBOL editor

**User Story:** As a Judge, I want full control of the COBOL source, so that I can test any program.

#### Acceptance Criteria

1. THE Frontend SHALL render the COBOL editor as editable by default, with no read-only lock.
2. THE Legacy_Panel SHALL provide an "Upload .cbl / .cob File" button that accepts `.cbl`, `.cob` and `.txt` files and loads the file text into the editor.
3. WHEN a Judge drops a `.cbl`, `.cob` or `.txt` file onto the 3270 terminal, THE Frontend SHALL load the file text into the editor.
4. THE Legacy_Panel SHALL provide a "Clear / Blank" button that empties the editor.
5. THE Frontend SHALL show top pills under the label "Starter Templates:" for Loan Amortization, Compound Interest, Daily Ledger and "+ Custom / Blank".
6. WHEN a Judge clicks a Starter_Template pill, THE Frontend SHALL load only the raw `.cbl` text into the editor and SHALL NOT display any hardcoded result.
7. THE Frontend SHALL provide a checkbox labeled "Bypass Session Cache (Force Live AI)" that sets `force_live_llm` in the resurrect request.
8. THE Legacy_Panel toolbar SHALL provide a "Load Test Sandbox Attack (CALL SYSTEM)" button that loads a COBOL program containing `CALL "SYSTEM"` into the editor.
9. WHEN the API returns HTTP 400 or 503, THE Frontend SHALL show the API message and SHALL NOT display pre-built results.

### Requirement 16: Dynamic cloud dashboard

**User Story:** As a Judge, I want the dashboard to adapt to whatever program I bring, so that no layout is hardcoded.

#### Acceptance Criteria

1. THE Cloud_Dashboard SHALL render sliders and exact decimal inputs from `dashboard_metadata.inputs` for any program, with no per-program layout code.
2. IF `dashboard_metadata.inputs` has 0 entries, THEN THE Cloud_Dashboard SHALL show a "Static Program — No Runtime Inputs" notice, the KPI cards and the raw stdout.
3. WHEN a Judge changes any input and 250 ms pass without a further change, THE Cloud_Dashboard SHALL call `POST /api/calculate-custom` with the resurrected `cobol_code`, `verified_python_code`, schema and `io_protocol`.
4. WHEN `/api/calculate-custom` returns, THE Frontend SHALL update both the KPI cards and the Live 3270 STDOUT in the Legacy_Panel.
5. THE Cloud_Dashboard SHALL send and keep input values as strings and SHALL NOT convert them to floating-point numbers.
6. THE Frontend SHALL pass the `npm run check:nofloat` check.

### Requirement 17: Dynamic chart

**User Story:** As a Judge, I want a chart that works for any outputs, so that results are visual without per-program code.

#### Acceptance Criteria

1. THE Dynamic_Chart SHALL render as native SVG, with no charting library.
2. THE Dynamic_Chart SHALL compute geometry with `BigInt` and no floating-point arithmetic.
3. THE Dynamic_Chart SHALL show a proportional horizontal bar comparison of the numeric outputs and inputs.
4. THE Dynamic_Chart SHALL draw a zero line and render positive and negative values on opposite sides of it.
5. WHERE an output has `format` equal to `"flag"`, THE Dynamic_Chart SHALL show a status badge for that output.

### Requirement 18: Export tab

**User Story:** As a Judge, I want to copy or download the microservice, so that I can inspect it.

#### Acceptance Criteria

1. THE Export tab SHALL display `export_fastapi_code` from the Resurrect_Response.
2. WHEN a Judge edits nothing and requests an export refresh, THE Export tab SHALL call `POST /api/export-custom` with `program_title`, `verified_python_code` and the schema.
3. THE Export tab SHALL provide copy and download actions for the returned code.

### Requirement 19: Dark fintech theme and layout

**User Story:** As a Judge, I want a consistent professional look, so that the product reads as a finished fintech app.

#### Acceptance Criteria

1. THE Themed_Components SHALL use `bg-[#0F172A]` containers, `bg-[#1E293B]` inner cards, `border-slate-800` or `border-slate-700` borders, and `text-slate-100` or `text-slate-400` text, matching FieldSliderInput and the dark emerald ParityBanner.
2. THE Legacy_Panel SHALL keep the `#0A0E0C` background and `#00FF66` text.
3. THE Legacy_Panel and CobolSourceEditor SHALL use `flex flex-col h-full`, with `flex-1` on the code area, to stretch to full height.
4. THE `.kiro/steering/frontend-design.md` file SHALL document the Dark_Fintech_Theme and the Legacy_Panel colors.

### Requirement 20: Health pill and pipeline status

**User Story:** As a Judge, I want a clean status display, so that cold start and stage status are clear.

#### Acceptance Criteria

1. WHEN the health response contains a compiler string such as "cobc (GnuCOBOL) 3.2.0", THE Health_Pill SHALL display only the product and version, as "GnuCOBOL 3.2.0 Online".
2. WHEN the page loads, THE Frontend SHALL call `GET {NEXT_PUBLIC_API_URL}/health` and show the themed warm-up status until the call succeeds.
3. THE PipelineStepper SHALL render the third stage as "3. Parity Auditor" with an emerald "live" badge and SHALL NOT render a "cache" badge for that stage.
4. WHILE the Pipeline is running, THE Frontend SHALL show the current stage name.

### Requirement 21: Audit trail rendering

**User Story:** As a Judge, I want counterexamples that are easy to read, so that I can see exactly what drifted.

#### Acceptance Criteria

1. THE AuditTrail SHALL render Inputs, GnuCOBOL Exact and Python Output as inline `key=value` chips.
2. THE AuditTrail SHALL apply `whitespace-nowrap` to each chip so that a pair such as `overdraft_threshold=...` never splits across lines.
3. THE AuditTrail SHALL allow line wrapping between chips.
4. THE AuditTrail SHALL apply `whitespace-nowrap` to the Float Drift badge.

### Requirement 22: README for judges

**User Story:** As a Judge, I want a clear root README, so that I understand the architecture and can run the project.

#### Acceptance Criteria

1. THE root `README.md` SHALL describe the universal COBOL-to-Cloud architecture and the three-agent Pipeline.
2. THE root `README.md` SHALL document the IEEE-754 float-drift proof in which `-0.01 + 50,000,000 - 50,000,000` equals `0.00` in float and `-0.01` in GnuCOBOL.
3. THE root `README.md` SHALL explain the use of Kiro features: `.kiro/steering`, `.kiro/agents`, `.kiro/specs`, `.kiro/hooks`, `.kiro/settings/mcp.json` and `.devcontainer`.
4. THE root `README.md` SHALL provide a local quick start with Docker for the Backend and Next.js for the Frontend.

### Requirement 23: Automated tests with mocked LLM

**User Story:** As a maintainer, I want the test suite to prove the universal behavior at zero token cost, so that regressions are caught.

#### Acceptance Criteria

1. THE file `backend/tests/test_universal_cobol.py` SHALL use a mocked LLM_Client and SHALL consume 0 real tokens and make no network call.
2. THE test suite SHALL cover a Payroll Tax Calculator in `single_run_lines` that compiles with real GnuCOBOL, infers the schema, runs universal differential verification and reaches 100% parity.
3. THE test suite SHALL cover a fixed-format 80-column program that compiles with real GnuCOBOL, infers the schema, runs universal differential verification and reaches 100% parity.
4. THE test suite SHALL cover `/api/calculate-custom` and `/api/export-custom` end to end with real GnuCOBOL.
5. THE test suite SHALL cover the Sandbox_Validator blocking `CALL "SYSTEM"` with HTTP 400 and 0 LLM calls.
6. THE test suite SHALL cover HTTP 503 on LLM_Unavailable with no bundle fallback.
7. THE test suite SHALL update every older test that expected a `preset_bundles.json` bypass, so that the whole suite passes with 0 failures in Docker.
8. THE task plan SHALL mark optional property-test sub-tasks as skipped.

### Requirement 24: Build and verification gates

**User Story:** As a maintainer, I want build gates to pass before delivery, so that the pushed code is releasable.

#### Acceptance Criteria

1. WHEN the delivery task runs, THE build process SHALL rebuild the Docker image with `docker build -t vericobol-backend ./backend` and run the full pytest suite in Docker with 0 failures.
2. WHEN the delivery task runs in `frontend/`, THE build process SHALL run `npm run check:nofloat`, `npx tsc --noEmit`, `npm run lint` and `npm run build`, and each SHALL exit with code 0.
3. WHEN the delivery task runs a smoke check, THE build process SHALL start a temporary Docker container on port 8001, check `/health` and the sandbox rejection, confirm that no `.env` content is exposed, then stop and remove the container.
4. THE Universal_Platform SHALL use `pathlib.Path` for paths, `decimal.Decimal` for every monetary value in `backend/app/`, and avoid `pandas`, `numpy`, `scipy` and `torch`.

### Requirement 25: Secrets hygiene and git delivery

**User Story:** As an operator, I want no secret committed, so that the repository stays safe.

#### Acceptance Criteria

1. THE delivery task SHALL NOT read, print or stage `.env`.
2. THE delivery task SHALL verify that `.gitignore` ignores `.env` and that `.env` is not staged before committing.
3. THE delivery task SHALL verify that `.env.example` contains only placeholder values and no real key.
4. WHEN the delivery task marks work complete, THE delivery task SHALL update the completed required checkboxes in `tasks.md`.
5. WHEN the checks pass, THE delivery task SHALL commit and push non-interactively with `GIT_TERMINAL_PROMPT="0"` and `GCM_INTERACTIVE="never"` using `git push origin main`.
