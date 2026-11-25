# Requirements Document

## Introduction

The Resurrection_Pipeline turns a legacy COBOL program into verified, exact-precision Python and a dashboard schema. It runs three stages in order (Legacy_Parser, Cloud_Architect, Parity_Auditor). The Parity_Auditor calls the existing `run_verification()` function, which compiles and runs the real COBOL program with GnuCOBOL and compares every output field with the generated Python.

This feature adds the `POST /api/resurrect` and `GET /api/export/{preset_id}` endpoints, a self-healing loop (at most 2 retries), a demonstration mode that shows a float-drift bug being repaired, a SHA-256 result cache with bundled preset results, an OpenAI-compatible LLM client with a $2 budget, a stdlib-only stdio MCP_Server, two Kiro hooks, a devcontainer, new tests, a Docker rebuild with a full test run, and a git commit and push.

Decisions that bound this feature:
- Custom COBOL requires a caller-supplied `field_schema`. The LLM stage produces only business rules, PIC mappings, dashboard metadata and Decimal Python code. It does not infer the input or output contract.
- When self-healing fails after 2 retries, `POST /api/resurrect` returns HTTP 200 with the full `audit_trail` and a failed `final_report`. The code is not marked certified.

## Glossary

- **Resurrection_Pipeline**: The backend component that runs Legacy_Parser, Cloud_Architect and Parity_Auditor in sequence and assembles the Resurrect_Response.
- **Legacy_Parser**: Stage 1. Extracts business rules and PIC mappings from COBOL source.
- **Cloud_Architect**: Stage 2. Generates typed Python 3.12 using `decimal.Decimal` and the dashboard metadata.
- **Parity_Auditor**: Stage 3. Calls `run_verification()` to compare the real COBOL output with the generated Python and drives self-healing.
- **Verification_Engine**: The existing `run_verification()` function in `backend/app/pbt_verifier.py` and the components it uses (CobolRunner, source validator, Hypothesis case generator).
- **Preset**: One of `loan_calc`, `compound_int`, `daily_ledger`, with COBOL source in `backend/cobol_samples/`.
- **Custom_COBOL**: COBOL source supplied by the caller in `cobol_code`.
- **Field_Schema**: Caller-supplied list describing each input and output field of Custom_COBOL (name, PIC, scale, direction).
- **LLM_Client**: The OpenAI-compatible client configured through `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL`.
- **LLM_Key_Missing**: The state in which `LLM_API_KEY` is unset, empty, or equal to a placeholder value from `.env.example`.
- **Self_Healing_Loop**: Re-generation of Python from a Counterexample after a failed attempt, at most 2 retries (3 attempts in total).
- **Counterexample**: The exact failing case reported by the Verification_Engine (inputs, COBOL output, Python output, differing field, likely cause).
- **Demo_Mode**: Operation with `demonstrate_self_healing=true`.
- **FLOAT_BUG_REGISTRY**: The existing registry in `backend/app/reference_models.py` holding float-based Python implementations per Preset.
- **Pipeline_Cache**: In-memory SHA-256 cache of Resurrect_Response results.
- **Preset_Bundle_File**: `backend/cobol_samples/preset_bundles.json`, holding pre-verified pipeline results for the three Presets.
- **Resurrect_Response**: The JSON body `{preset_id, cache_hit, llm_used, stages, business_rules, pic_mappings, dashboard_metadata, verified_python_code, export_fastapi_code, audit_trail, final_report}`.
- **Audit_Trail**: Ordered list of records, one per stage execution and per verification attempt, in the Resurrect_Response.
- **Final_Report**: The Parity_Report of the last verification attempt plus a `certified` boolean and a `status` of `passed` or `failed`.
- **Certified**: A Final_Report state in which `status` is `passed` and parity score is 100.00.
- **MCP_Server**: The stdlib-only stdio Model Context Protocol server at `backend/mcp_server/server.py`.
- **MCP_Config**: `.kiro/settings/mcp.json`.
- **Hook_Files**: `.kiro/hooks/guard-no-float.kiro.hook` and `.kiro/hooks/verify-cobol-parity.kiro.hook`.
- **Devcontainer**: `.devcontainer/devcontainer.json`.
- **Backend_Image**: The Docker image `vericobol-backend` built from `backend/Dockerfile`.
- **Placeholder_Key**: A value that equals a placeholder in `.env.example` (for example `changeme` or `your-api-key-here`).

## Requirements

### Requirement 1: Three-stage pipeline orchestration

**User Story:** As a bank modernization engineer, I want the legacy COBOL passed through parser, architect and auditor stages, so that I receive Python code that is proven equal to the original.

#### Acceptance Criteria

1. WHEN a valid resurrect request is accepted, THE Resurrection_Pipeline SHALL execute Legacy_Parser, Cloud_Architect and Parity_Auditor in that order.
2. THE Resurrection_Pipeline SHALL report each stage in the `stages` field with its name, status (`ok`, `failed` or `skipped`), elapsed milliseconds and a `source` of `llm`, `cache` or `deterministic`.
3. THE Parity_Auditor SHALL obtain the Final_Report by calling the existing `run_verification()` function with the generated Python code.
4. THE Resurrection_Pipeline SHALL pass the request `num_cases` value to `run_verification()` unchanged.
5. WHEN the Legacy_Parser completes, THE Resurrection_Pipeline SHALL include `business_rules` as a list of plain-English strings and `pic_mappings` as a list of objects holding field name, PIC clause, scale and rounding mode.
6. WHEN the Cloud_Architect completes, THE Resurrection_Pipeline SHALL include `verified_python_code` as Python source that uses `decimal.Decimal` with an explicit `decimal.Context`.
7. WHEN the Cloud_Architect completes, THE Resurrection_Pipeline SHALL include `dashboard_metadata` as JSON describing input controls (name, label, type, min, max, step) and output displays (name, label, format).
8. THE Resurrection_Pipeline SHALL pass `export_fastapi_code` as a self-contained FastAPI module source that wraps `verified_python_code`.
9. THE Resurrection_Pipeline SHALL contain no `float` type, float literal or `math.pow` in `verified_python_code` and `export_fastapi_code`.

### Requirement 2: Custom COBOL contract and LLM scope

**User Story:** As an API caller, I want to supply the field schema for my own COBOL, so that the system does not guess my input and output contract.

#### Acceptance Criteria

1. WHEN a request contains `cobol_code` without `field_schema`, THE Resurrection_Pipeline SHALL return an HTTP 4xx response with a validation error that names `field_schema` as required.
2. WHEN a request contains `field_schema` that is empty or has a field with a missing name, PIC or direction, THE Resurrection_Pipeline SHALL return an HTTP 4xx validation error identifying the offending field.
3. THE LLM_Client SHALL be used only to produce business rules, PIC mappings, dashboard metadata and Decimal Python code.
4. THE Resurrection_Pipeline SHALL take the input and output field contract exclusively from the Preset catalog or from the caller-supplied `field_schema`.
5. WHEN a request contains neither `preset_id` nor `cobol_code`, THE Resurrection_Pipeline SHALL return an HTTP 4xx validation error.
6. WHEN a request contains an unknown `preset_id`, THE Resurrection_Pipeline SHALL return an HTTP 4xx error that lists the valid Preset identifiers.
7. WHEN a request contains `num_cases` outside the range 150 to 200, THE Resurrection_Pipeline SHALL return an HTTP 4xx validation error.
8. WHEN a request contains a float literal for a monetary value in `field_schema` bounds, THE Resurrection_Pipeline SHALL return an HTTP 4xx validation error.
9. WHEN Custom_COBOL is rejected by the COBOL sandbox validator, THE Resurrection_Pipeline SHALL return an HTTP 4xx error with the validator message and SHALL NOT invoke the LLM_Client.

### Requirement 3: Self-healing loop

**User Story:** As a compliance reviewer, I want failed parity to trigger automatic repair with the exact failing case, so that the final code reaches penny parity without hand edits.

#### Acceptance Criteria

1. WHEN a verification attempt reports a parity score below 100.00, THE Parity_Auditor SHALL pass the first Counterexample to the Cloud_Architect and request repaired Python code.
2. THE Self_Healing_Loop SHALL perform at most 2 retries after the initial attempt.
3. WHEN a repaired code attempt is produced, THE Parity_Auditor SHALL verify it by a new call to `run_verification()` against the same COBOL source.
4. WHEN a verification attempt reports a parity score of 100.00, THE Parity_Auditor SHALL stop the Self_Healing_Loop and mark the Final_Report Certified.
5. THE Audit_Trail SHALL contain one record per verification attempt with attempt number, parity score, passed and total case counts, and the Counterexample when the attempt failed.
6. IF the Self_Healing_Loop reaches 2 retries without a parity score of 100.00, THEN THE Resurrection_Pipeline SHALL return HTTP 200 with the full Audit_Trail and a Final_Report whose `status` is `failed` and `certified` is `false`.
7. IF the Self_Healing_Loop fails, THEN THE Resurrection_Pipeline SHALL set `verified_python_code` to the last attempted code and SHALL NOT mark it Certified.
8. IF `run_verification()` raises a `VerificationError`, THEN THE Resurrection_Pipeline SHALL return an HTTP error response carrying the error code and message from the Verification_Engine and SHALL NOT start a retry.

### Requirement 4: Demonstration mode

**User Story:** As a hackathon judge, I want to see a float-drift bug caught and repaired, so that I can see the proof system working.

#### Acceptance Criteria

1. WHERE `demonstrate_self_healing` is true and the request targets a Preset, THE Parity_Auditor SHALL use the FLOAT_BUG_REGISTRY implementation of that Preset as the code for attempt 1.
2. WHERE `demonstrate_self_healing` is true, THE Parity_Auditor SHALL verify attempt 1 through `run_verification()` and record the resulting Counterexample in the Audit_Trail.
3. WHERE `demonstrate_self_healing` is true, THE Parity_Auditor SHALL repair attempt 1 by replacing it with the Decimal implementation and verify the repair as attempt 2.
4. WHERE `demonstrate_self_healing` is true, THE Resurrection_Pipeline SHALL return a Final_Report with a parity score of 100.00 and an Audit_Trail containing at least 2 attempt records.
5. WHERE `demonstrate_self_healing` is true and the request carries Custom_COBOL without a Preset float-bug implementation, THE Resurrection_Pipeline SHALL return an HTTP 4xx validation error stating that demonstration mode requires a Preset.
6. WHEN the Verification_Engine reports a parity score of 100.00 for the FLOAT_BUG_REGISTRY code of attempt 1, THE Resurrection_Pipeline SHALL record the attempt as passed and omit the repair step.

### Requirement 5: Caching and preset bundles

**User Story:** As the project owner, I want repeated preset clicks to cost no LLM tokens while still proving parity live, so that the demo stays within budget.

#### Acceptance Criteria

1. THE Pipeline_Cache SHALL key each entry on the SHA-256 digest of the pair (normalized COBOL source, `demonstrate_self_healing`).
2. THE Pipeline_Cache SHALL normalize COBOL source by converting line endings to `\n`, stripping trailing whitespace on each line and removing leading and trailing blank lines before hashing.
3. THE Preset_Bundle_File SHALL contain for each of the three Presets and each value of `demonstrate_self_healing` the stored `business_rules`, `pic_mappings`, `dashboard_metadata`, `verified_python_code`, `export_fastapi_code` and stage records.
4. WHEN a request hits the Pipeline_Cache or the Preset_Bundle_File, THE Resurrection_Pipeline SHALL set `cache_hit` to true, set `llm_used` to false and make zero LLM_Client calls.
5. WHEN a request hits the Pipeline_Cache or the Preset_Bundle_File, THE Parity_Auditor SHALL still call `run_verification()` against the real COBOL program and use that fresh result as Final_Report.
6. WHEN the stored code fails the fresh verification on a cache hit, THE Resurrection_Pipeline SHALL return the failed Final_Report with `certified` set to false.
7. WHERE `force_live_llm` is true and the request targets a Preset, THE Resurrection_Pipeline SHALL bypass the Pipeline_Cache and the Preset_Bundle_File and call the LLM_Client.
8. WHEN a live LLM run reaches a Final_Report with `certified` true, THE Resurrection_Pipeline SHALL store the result in the Pipeline_Cache.
9. THE Pipeline_Cache SHALL hold at most 64 entries and evict the least recently used entry when the limit is exceeded.
10. THE Resurrection_Pipeline SHALL answer `GET /health` without calling the LLM_Client, the compiler or the Pipeline_Cache.

### Requirement 6: LLM client and budget

**User Story:** As the project owner, I want the LLM configured only by environment variables and capped at $2, so that no secret leaks and no overspend occurs.

#### Acceptance Criteria

1. THE LLM_Client SHALL read its endpoint, key and model only from `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL`.
2. THE Resurrection_Pipeline SHALL contain no hardcoded API key or endpoint in source, tests, hooks, MCP_Config or Docker files.
3. THE `.env.example` file SHALL contain placeholder values for `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `FRONTEND_URL`, `NEXT_PUBLIC_API_URL` and `COBC_PATH`.
4. WHEN the request carries Custom_COBOL and LLM_Key_Missing holds, THE Resurrection_Pipeline SHALL return HTTP 503 with a body naming `LLM_API_KEY` as the missing configuration.
5. WHEN the request targets a Preset, `force_live_llm` is false and the Preset_Bundle_File holds the Preset, THE Resurrection_Pipeline SHALL complete without reading `LLM_API_KEY`.
6. WHERE `force_live_llm` is true and LLM_Key_Missing holds, THE Resurrection_Pipeline SHALL return HTTP 503.
7. THE LLM_Client SHALL send one request per stage call, with a timeout of 30 seconds and a response size cap of 64 KiB.
8. THE Resurrection_Pipeline SHALL treat LLM output as untrusted and SHALL load generated Python only through the existing restricted loader in the Verification_Engine.
9. IF an LLM call times out or returns an unparseable response, THEN THE Resurrection_Pipeline SHALL return HTTP 502 with the failing stage name and SHALL NOT include the API key or raw provider response in the body.
10. THE Resurrection_Pipeline SHALL track cumulative token cost per process and WHEN the cumulative cost reaches $2.00, THE Resurrection_Pipeline SHALL reject further live LLM calls with HTTP 503 and a budget-exhausted message.
11. THE Resurrection_Pipeline SHALL exclude `pandas`, `numpy`, `scipy`, `torch` and other heavy ML frameworks from `backend/requirements.txt`.

### Requirement 7: Resurrect endpoint contract

**User Story:** As a frontend developer, I want one endpoint with a stable response shape, so that the dashboard can render every result.

#### Acceptance Criteria

1. THE Resurrection_Pipeline SHALL expose `POST /api/resurrect` accepting `{preset_id?, cobol_code?, field_schema?, num_cases=150, demonstrate_self_healing=false, force_live_llm=false}`.
2. WHEN a request is valid and processing completes, THE Resurrection_Pipeline SHALL return HTTP 200 with a Resurrect_Response that contains all eleven fields `preset_id`, `cache_hit`, `llm_used`, `stages`, `business_rules`, `pic_mappings`, `dashboard_metadata`, `verified_python_code`, `export_fastapi_code`, `audit_trail` and `final_report`.
3. WHEN a request contains both `preset_id` and `cobol_code`, THE Resurrection_Pipeline SHALL return an HTTP 4xx validation error.
4. THE Resurrection_Pipeline SHALL serialize every monetary value in the Resurrect_Response as a JSON string.
5. THE Resurrection_Pipeline SHALL parse request JSON numbers with `parse_float=Decimal` and reject float monetary values.
6. WHEN the request body exceeds 256 KiB, THE Resurrection_Pipeline SHALL return HTTP 413.
7. THE Resurrection_Pipeline SHALL serialize concurrent verification runs so that only one COBOL compile-and-run executes at a time.
8. THE Resurrection_Pipeline SHALL return `preset_id` as null for Custom_COBOL.
9. THE Resurrection_Pipeline SHALL complete the verification step of a Preset request with 150 cases within 3 seconds on a 0.1 CPU allocation.

### Requirement 8: Export endpoint

**User Story:** As a dashboard user, I want to download the verified Python and API, so that I can run the modern version locally.

#### Acceptance Criteria

1. THE Resurrection_Pipeline SHALL expose `GET /api/export/{preset_id}`.
2. WHEN `preset_id` names a Preset with a bundled certified result, THE Resurrection_Pipeline SHALL return HTTP 200 with the `verified_python_code` and `export_fastapi_code` of that Preset and a `certified` flag.
3. IF `preset_id` is not a Preset identifier, THEN THE Resurrection_Pipeline SHALL return HTTP 404 with the list of valid Preset identifiers.
4. THE Resurrection_Pipeline SHALL serve the export from the Preset_Bundle_File and SHALL NOT call the LLM_Client or the compiler.

### Requirement 9: MCP server

**User Story:** As a Kiro IDE user, I want a local GnuCOBOL MCP server, so that agents can compile, run and verify COBOL from the editor.

#### Acceptance Criteria

1. THE MCP_Server SHALL be implemented in `backend/mcp_server/server.py` using only the Python standard library and the existing `backend/app` modules.
2. THE MCP_Server SHALL communicate over stdio using newline-delimited JSON-RPC 2.0.
3. WHEN the MCP_Server receives `initialize`, THE MCP_Server SHALL respond with its protocol version, capabilities that include `tools`, and server name `vericobol-gnucobol`.
4. WHEN the MCP_Server receives `tools/list`, THE MCP_Server SHALL return exactly the tools `list_cobol_presets`, `compile_and_run_cobol` and `verify_cobol_parity`, each with a JSON input schema.
5. WHEN the MCP_Server receives `tools/call` for `list_cobol_presets`, THE MCP_Server SHALL return the identifiers, titles and input and output field names of the three Presets.
6. WHEN the MCP_Server receives `tools/call` for `compile_and_run_cobol` with COBOL source and input rows, THE MCP_Server SHALL validate the source, compile it and run it in a fresh temporary directory, and return stdout, stderr and exit code.
7. WHEN the MCP_Server receives `tools/call` for `verify_cobol_parity` with a `preset_id`, THE MCP_Server SHALL call `run_verification()` and return the parity score, passed and total case counts and the Counterexamples.
8. IF a tool call carries source rejected by the sandbox validator, THEN THE MCP_Server SHALL return a tool result with `isError` true and the validator message.
9. IF the MCP_Server receives an unknown method, THEN THE MCP_Server SHALL return JSON-RPC error code -32601.
10. IF the MCP_Server receives a line that is not valid JSON, THEN THE MCP_Server SHALL return JSON-RPC error code -32700 and continue reading.
11. THE MCP_Server SHALL write only JSON-RPC messages to stdout and write diagnostics to stderr.
12. THE MCP_Config SHALL define a server named `vericobol-gnucobol` whose command runs the MCP_Server inside the Backend_Image through `docker run -i --rm`.
13. THE MCP_Config SHALL contain no secrets and no API keys.

### Requirement 10: Kiro hooks

**User Story:** As a maintainer, I want hooks that guard precision and run parity tests, so that regressions are caught while I edit.

#### Acceptance Criteria

1. THE Hook_Files SHALL include `.kiro/hooks/guard-no-float.kiro.hook` and `.kiro/hooks/verify-cobol-parity.kiro.hook`, both valid JSON.
2. WHEN a Python file under `backend/app/` or `backend/cobol_samples/` is saved, THE guard-no-float hook SHALL scan the file for `float(`, float literals and `math.pow` in monetary code and report each match with its line number.
3. WHEN a file under `backend/cobol_samples/` with extension `.cbl` is saved, THE verify-cobol-parity hook SHALL run the parity tests for the Presets.
4. THE Hook_Files SHALL use argument-safe commands and contain no secrets.

### Requirement 11: Devcontainer

**User Story:** As a Windows developer, I want a Linux devcontainer with GnuCOBOL, so that I can run the real compiler locally.

#### Acceptance Criteria

1. THE Devcontainer SHALL reference `backend/Dockerfile` as its build definition.
2. THE Devcontainer SHALL provide the `cobc` executable on `PATH` and set `COBC_PATH` to `cobc`.
3. THE Devcontainer SHALL install the Python dependencies from `backend/requirements.txt`.
4. THE Devcontainer SHALL forward port 8000.
5. THE Devcontainer SHALL contain no secrets.

### Requirement 12: Automated tests

**User Story:** As a maintainer, I want automated tests for the pipeline and the MCP server, so that the contract stays correct without live LLM spend.

#### Acceptance Criteria

1. THE test suite SHALL include `backend/tests/test_resurrection_pipeline.py` and `backend/tests/test_mcp_server.py`.
2. THE tests SHALL replace the LLM_Client with a deterministic stub so that no network call and no token cost occurs.
3. THE test_resurrection_pipeline tests SHALL cover each Preset with `cache_hit` true, `llm_used` false and `certified` true.
4. THE test_resurrection_pipeline tests SHALL cover Demo_Mode, asserting a failed attempt 1 with a Counterexample, a passing attempt 2 and a final parity score of 100.00.
5. THE test_resurrection_pipeline tests SHALL cover healing failure, asserting HTTP 200, 3 attempt records, `status` failed and `certified` false.
6. THE test_resurrection_pipeline tests SHALL cover the 4xx missing `field_schema` error, the 503 missing-key error, the cache key rule and the 404 export error.
7. THE test_mcp_server tests SHALL cover `initialize`, `tools/list`, each of the three tools, an unknown method and malformed JSON.
8. THE tests SHALL assert that no `float` appears in `verified_python_code` and `export_fastapi_code` of any bundled result.
9. THE tests SHALL assert that every Preset bundle in the Preset_Bundle_File passes live verification with parity score 100.00.

### Requirement 13: Docker rebuild and full test run

**User Story:** As a release owner, I want the image rebuilt and the whole suite run inside it, so that the result is verified on the deployment platform.

#### Acceptance Criteria

1. WHEN implementation is complete, THE release process SHALL run `docker build -t vericobol-backend ./backend` and the build SHALL exit with code 0.
2. WHEN the image build completes, THE release process SHALL run the full pytest suite inside the Backend_Image and the run SHALL report 0 failures and 0 errors.
3. THE Backend_Image SHALL include `backend/mcp_server/` and `backend/cobol_samples/preset_bundles.json`.
4. THE Backend_Image SHALL stay based on `python:3.12-slim` with GnuCOBOL installed.
5. IF the Docker build or the test run fails, THEN THE release process SHALL fix the cause and rerun both steps before committing.

### Requirement 14: Commit and push

**User Story:** As the project owner, I want all work committed and pushed non-interactively, so that the submission is on the remote.

#### Acceptance Criteria

1. WHEN Requirement 13 is satisfied, THE release process SHALL commit all new and changed files, including `.kiro/` content and the synced `.kiro/specs/cobol-verification-engine/tasks.md`.
2. THE release process SHALL exclude `.env`, `.hypothesis/` and other ignored files from the commit.
3. THE release process SHALL push with `$env:GIT_TERMINAL_PROMPT="0"; $env:GCM_INTERACTIVE="never"` set in the shell so that no credential prompt blocks the push.
4. IF the push fails, THEN THE release process SHALL report the git error output to the user and SHALL NOT use force push.
5. WHEN the push succeeds, THE release process SHALL confirm that the local HEAD commit hash equals the remote branch head.
