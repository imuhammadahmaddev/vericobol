# Implementation Plan: Universal Open COBOL Platform

## Overview

Implementation language: Python 3.12 (Backend, FastAPI) and TypeScript (Frontend, Next.js). The plan removes the preset bypass, adds contract inference and protocol-aware verification, adds the two custom endpoints, rebuilds the dashboard around `dashboard_metadata`, updates older tests, and ends with the Docker, frontend, smoke and secret-safe git delivery gates.

Rules for every task:
- Never read, print or stage `.env`. Stage files by explicit name, never `git add -A`.
- Use `pathlib.Path` for paths and `decimal.Decimal` for money. No `pandas`, `numpy`, `scipy` or `torch`.
- `preset_bundles.json` and `reference_models.py` stay in the repository but are imported only from `backend/tests/`.
- Optional property-test sub-tasks are marked `*` and are skipped in the required plan (Requirement 23.8).

## Tasks

- [x] 1. LLM client: env config, budget, parsing, contract inference
  - [x] 1.1 Rework `backend/app/llm_client.py`
    - Set prices to `Decimal("0.10")` and `Decimal("0.50")` per 1M tokens, cap `Decimal("2.00")` with `LLM_BUDGET_USD` override (invalid value falls back to 2.00)
    - Read endpoint, key and model only from `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`
    - Strip ```` ```json ```` and ```` ``` ```` fences, then `json.loads(..., parse_float=Decimal)`
    - Add `LLMUnavailable` with cause `missing_key`, `provider_failure` or `budget_exhausted`; error text built from fixed strings, never containing the key
    - Add `infer_contract` (returns `InferredProgram`, one retry with the validation error, then `LLMCallError`), `generate_python` (with `float_demo` flag) and `repair_python`
    - _Requirements: 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 6.1, 6.2, 6.3, 8.5_

  - [x] 1.2 Update `.env.example`
    - Set `LLM_MODEL=gpt-6-luna`; keep placeholders only for `LLM_BASE_URL`, `LLM_API_KEY` and all other secrets
    - Confirm `.gitignore` ignores `.env` (inspect `.gitignore` only, never `.env`)
    - _Requirements: 1.1, 25.2, 25.3_

  - [ ]* 1.3 Write property tests for the LLM client
    - **Property 1: Budget accounting is exact and capped**
    - **Property 2: Reply parsing strips fences and preserves Decimal**
    - **Property 3: Errors never leak the key**
    - File: `backend/tests/test_llm_client_properties.py`
    - **Validates: Requirements 1.3, 1.4, 1.5, 1.6, 1.7, 1.8**

- [x] 2. Sandbox validator and COBOL runner
  - [x] 2.1 Rework `validate_source()` in `backend/app/cobol_runner.py`
    - Reject `CALL`, `SYSTEM`, `SELECT ... ASSIGN`, `OPEN`, `READ`, `WRITE`, `REWRITE`, `DELETE`, `COPY`, `REPLACE`, `ACCEPT ... FROM ENVIRONMENT|COMMAND-LINE`, `DISPLAY ... UPON ENVIRONMENT`
    - Report construct name and line number; skip fixed-format comment lines (`*` in column 7); enforce the source size cap (413)
    - _Requirements: 3.1, 3.2, 3.4, 3.5, 3.6, 3.7_

  - [x] 2.2 Rework `CobolRunner` in `backend/app/cobol_runner.py`
    - `compile_source`: `cobc -x -free`, one fallback to fixed-format `cobc -x`, return `source_format` (`free` or `fixed`) and last diagnostics on failure
    - `COBC_PATH` resolution, 5 s timeouts, argument lists, fresh temp directory cleaned in `finally`, stdout size cap
    - `BinaryCache`: `OrderedDict` LRU, 16 entries, key SHA-256 of `cobol_source`, eviction deletes files, shutdown cleanup; `get_binary` re-validates the source
    - `run_batch` and `run_single` against a cached binary
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 3.7, 4.1, 4.2, 4.3, 4.4, 4.5_

  - [ ]* 2.3 Write property tests for sandbox and binary cache
    - **Property 4: Sandbox validator blocks prohibited constructs on executable lines**
    - **Property 6: Binary cache is bounded, LRU, and compile-once**
    - File: `backend/tests/test_sandbox_cache_properties.py`
    - **Validates: Requirements 3.2, 3.4, 3.5, 4.1, 4.2, 4.3, 4.4**

- [x] 3. Pipeline cache and export template
  - [x] 3.1 Rework `backend/app/pipeline_cache.py`
    - `normalize_cobol`: line endings, comment lines, whitespace, keyword case outside literals; idempotent
    - `cache_key(source, demonstrate_self_healing)` as SHA-256 of normalized bytes plus a flag byte
    - Thread-safe LRU that starts empty (no seeding); `put` only for Certified live-LLM results
    - _Requirements: 10.1, 10.2, 10.3, 10.6_

  - [x] 3.2 Update `backend/app/export_template.py`
    - Keep `render(verified_python_code, schema)`; add an `ast` post-render check that raises `ValueError` on `float` names, float constants or `math.pow`
    - _Requirements: 6.5, 13.2, 13.3_

  - [ ]* 3.3 Write property test for cache key normalization
    - **Property 7: Cache key normalization**
    - File: `backend/tests/test_cache_key_properties.py`
    - **Validates: Requirements 10.1, 10.3**

  - [ ]* 3.4 Write property test for exported modules
    - **Property 10: Exported modules are float-free and well-formed**
    - File: `backend/tests/test_export_properties.py`
    - **Validates: Requirements 6.5, 13.2, 13.3**

- [x] 4. Universal Verification_Engine
  - [x] 4.1 Rework `backend/app/pbt_verifier.py` with protocol adapters
    - Adapters for `batch_space` (150 cases, one batched call), `single_run_lines` and `single_run_space` (35 to 50 cases, cached binary), `no_input_static` (one run)
    - Case generation within field min, max and PIC capacity, including bounds, 0.00, 0.01, negative values for signed `S9` fields and half-cent decimals
    - Compare every output field with difference 0; compile once per run; Counterexample with inputs, COBOL output, Python output, field and likely cause
    - Restricted namespace execution; `allow_float_demo=True` path converts results with `Decimal(str(x))` and reports exact drift
    - _Requirements: 6.4, 6.6, 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.9, 7.10, 8.5, 8.6_

  - [ ]* 4.2 Write property tests for the Verification_Engine
    - **Property 11: Verification compares exactly and compiles once**
    - **Property 12: Case generation honors the protocol and field bounds**
    - **Property 15: Float demo drift is measured exactly**
    - File: `backend/tests/test_verification_engine_properties.py`
    - **Validates: Requirements 7.1, 7.2, 7.4, 7.5, 7.6, 7.9, 8.6, 8.7**

- [x] 5. Pipeline orchestration without bundle bypass
  - [x] 5.1 Rework `backend/app/resurrection.py`
    - Remove `seed_cache`, `load_preset_bundles`, `reference_decimal_source`, `contract_from_preset` and every `reference_models` import
    - Run Legacy_Parser, Cloud_Architect, Parity_Auditor; enforce zero inputs only for `no_input_static`, else 422
    - `SelfHealingLoop` (max 2 retries, stop at Certified, first Counterexample sent to `repair_python`, `certified=false` with HTTP 200 after the last mismatch)
    - Demo mode: attempt 1 naive float translation, attempt 2 repair from the concrete Counterexample, no program-specific code path
    - Float constructs outside the demo attempt count as a failed attempt (`guard_no_float`)
    - Pipeline_Cache lookup unless `force_live_llm`; on hit skip stages 1 and 2 and still run live verification; Parity_Auditor records `source="live"`; audit trail with one record per stage execution and per attempt
    - Map errors: `LLMUnavailable` to 503, `LLMCallError` to 502, incomplete contract to 422
    - _Requirements: 5.9, 5.10, 6.4, 7.7, 7.8, 8.1, 8.2, 8.3, 8.4, 8.5, 8.7, 8.8, 8.9, 9.1, 9.2, 9.4, 9.5, 10.2, 10.4, 10.5_

  - [ ]* 5.2 Write property tests for the pipeline
    - **Property 8: Only Certified live results enter the Pipeline_Cache**
    - **Property 9: Float constructs are rejected outside the float demo**
    - **Property 13: Self-healing is bounded and stops at certification**
    - **Property 14: Audit trail has one record per execution**
    - File: `backend/tests/test_pipeline_properties.py`
    - **Validates: Requirements 6.4, 7.7, 7.8, 8.1, 8.2, 8.3, 8.8, 8.9, 10.2, 10.4**

- [x] 6. API endpoints
  - [x] 6.1 Update `backend/app/main.py` for resurrect, presets and health
    - `POST /api/resurrect` with `cobol_code` or `preset_id`, `num_cases` (150), `demonstrate_self_healing`, `force_live_llm`; validate source first (400/413); unknown `preset_id` returns 404
    - `GET /api/presets` returns ids, titles and raw COBOL only
    - `GET /health` adds `llm_configured: bool` and a cached compiler version, with no LLM or compiler call
    - Remove preset-only endpoints and `_warm_presets`; parse request numbers with `parse_float=Decimal`; map LLM errors to 503/502; CORS from `FRONTEND_URL`
    - _Requirements: 3.1, 3.3, 3.6, 9.4, 9.5, 11.1, 11.2, 11.3, 11.4, 11.5, 14.1, 14.2, 14.3_

  - [x] 6.2 Add `POST /api/calculate-custom` and `POST /api/export-custom` in `backend/app/main.py`
    - `calculate-custom`: `validate_source` (400), per-field range and PIC capacity check (422 naming the field), cached binary run plus restricted Python, compare every output with difference 0, return `result`, `raw_cobol_output`, `parity_verified`
    - `export-custom`: return `filename`, `verified_python_code`, `export_fastapi_code` (via `export_template.render()`) and `certified`
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 13.1, 13.2, 13.3_

  - [x] 6.3 Remove remaining runtime uses of retired artifacts
    - Grep `backend/app/` for `reference_models` and `preset_bundles`; remove any runtime import or read; keep both files in the repository for tests
    - Confirm no `float` money types or banned libraries in `backend/app/` and `backend/requirements.txt`
    - _Requirements: 9.1, 9.3, 9.5, 24.4_

  - [ ]* 6.4 Write property tests for the API
    - **Property 5: Rejected sources reach no LLM and no compiler**
    - **Property 16: Calculate-custom validates inputs and proves parity**
    - **Property 17: Unavailable LLM never falls back to bundles**
    - **Property 18: Money values never become floats**
    - File: `backend/tests/test_universal_api_properties.py`
    - **Validates: Requirements 3.1, 3.3, 9.2, 9.4, 9.5, 11.5, 12.2, 12.3, 12.4, 12.5**

- [x] 7. Checkpoint - Backend complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Frontend
  - [x] 8.1 Update types, API client, hooks and helpers
    - `frontend/src/lib/types.ts`, `lib/api.ts` (reads `NEXT_PUBLIC_API_URL`), `lib/money.ts`, `lib/chartGeometry.ts` (`BigInt` only), `lib/sandboxSnippet.ts`
    - Hooks `useResurrect` (stage status, 400/503 error mapping), `useCalculate` (250 ms debounce, string values, publishes stdout), `useHealth` (formats `cobc (GnuCOBOL) 3.2.0` as `GnuCOBOL 3.2.0 Online`)
    - _Requirements: 15.9, 16.3, 16.4, 16.5, 16.6, 20.1, 20.2_

  - [x] 8.2 Build the Legacy_Panel and Starter Templates
    - `CobolSourceEditor` editable by default, `flex flex-col h-full` with `flex-1` code area, `#0A0E0C` background and `#00FF66` text
    - Toolbar: "Upload .cbl / .cob File" (`.cbl`, `.cob`, `.txt`), drag-and-drop onto the 3270 terminal, "Clear / Blank", "Bypass Session Cache (Force Live AI)" checkbox setting `force_live_llm`, "Load Test Sandbox Attack (CALL SYSTEM)"
    - `PresetTabs` under "Starter Templates:" with Loan Amortization, Compound Interest, Daily Ledger and "+ Custom / Blank"; load raw `.cbl` text only
    - Show the API message for HTTP 400 and 503 and no results (`RejectionDetail`)
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5, 15.6, 15.7, 15.8, 15.9, 19.2, 19.3_

  - [x] 8.3 Build the dynamic Cloud_Dashboard and chart
    - `CloudDashboard.tsx`: sliders and exact decimal inputs from `dashboard_metadata.inputs`, KPI cards, "Static Program — No Runtime Inputs" notice with KPI cards and raw stdout when inputs are empty; values stay strings
    - `DynamicChart.tsx`: native SVG, `BigInt` geometry, proportional horizontal bars, zero line with positive and negative sides, status badge for `flag` outputs
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5, 17.1, 17.2, 17.3, 17.4, 17.5_

  - [x] 8.4 Update export, audit, stepper, health and theme components
    - Export tab and `VerifiedPythonTab`: show `export_fastapi_code`, refresh via `/api/export-custom`, copy and download
    - `AuditTrail`: `key=value` chips with `whitespace-nowrap`, wrapping between chips, `whitespace-nowrap` on the Float Drift badge
    - `PipelineStepper`: "3. Parity Auditor" with emerald "live" badge, no "cache" badge, current stage name while running
    - `HealthPill`, `ColdStartBanner`: themed warm-up status until `/health` succeeds
    - Apply Dark_Fintech_Theme classes to all Themed_Components
    - _Requirements: 18.1, 18.2, 18.3, 19.1, 20.1, 20.2, 20.3, 20.4, 21.1, 21.2, 21.3, 21.4_

  - [x] 8.5 Document the theme in `.kiro/steering/frontend-design.md`
    - Record Dark_Fintech_Theme classes and the Legacy_Panel colors
    - _Requirements: 19.4_

  - [x] 8.6 Wire `frontend/src/app/page.tsx`
    - Connect Legacy_Panel, PresetTabs, PipelineStepper, CloudDashboard, ExportTab, AuditTrail and health hooks; calculate-custom results update KPI cards and the Live 3270 STDOUT
    - _Requirements: 15.6, 16.3, 16.4, 20.2, 20.4_

  - [ ]* 8.7 Write frontend tests
    - **Property 19: Chart geometry is exact and sign-correct** (`lib/chartGeometry` test, `fast-check` optional)
    - Example tests: editable editor, pills load raw text only, force-live checkbox, 400/503 display, static-program notice, debounce, Health_Pill text, stepper badges, chip `whitespace-nowrap`
    - **Validates: Requirements 15.1, 15.6, 15.7, 15.9, 16.2, 16.3, 17.2, 17.3, 17.4, 20.1, 20.3, 21.2**

- [x] 9. Update older backend tests
  - [x] 9.1 Rewrite tests that expected the `preset_bundles.json` bypass
    - Review `backend/tests/` (for example `test_api_endpoints.py`, `test_pipeline_orchestration.py`, `test_resurrection_pipeline.py`, `test_resurrect_api.py`, `test_self_healing_loop.py`, `test_catalog.py`, `test_mcp_server.py`) and switch them to a scripted fake LLM
    - Keep `preset_bundles.json` and `reference_models.py` only as Test_Oracles under `backend/tests/`
    - Remove or rewrite tests for dropped preset-only endpoints
    - _Requirements: 9.3, 23.7_

- [x] 10. Universal tests with a mocked LLM
  - [x] 10.1 Create `backend/tests/test_universal_cobol.py` fixtures and program tests
    - `FakeLLM` returning scripted `InferredProgram` and `ArchitectOutput`; no network, 0 tokens
    - Payroll Tax Calculator in `single_run_lines`: real GnuCOBOL compile, inferred schema, universal differential verification, 100% parity
    - Fixed-format 80-column program: real GnuCOBOL compile, inferred schema, verification, 100% parity
    - _Requirements: 23.1, 23.2, 23.3_

  - [x] 10.2 Extend `backend/tests/test_universal_cobol.py` with endpoint and failure tests
    - `/api/calculate-custom` and `/api/export-custom` end to end with real GnuCOBOL
    - Sandbox: `CALL "SYSTEM"` returns 400 with 0 LLM calls
    - HTTP 503 on LLM_Unavailable with no bundle fallback
    - _Requirements: 23.1, 23.4, 23.5, 23.6_

- [x] 11. README
  - [x] 11.1 Update root `README.md`
    - Describe the universal COBOL-to-Cloud architecture and the three-agent Pipeline
    - Document the float-drift proof: `-0.01 + 50,000,000 - 50,000,000` is `0.00` in float and `-0.01` in GnuCOBOL
    - Explain `.kiro/steering`, `.kiro/agents`, `.kiro/specs`, `.kiro/hooks`, `.kiro/settings/mcp.json` and `.devcontainer`
    - Add a local quick start: Docker for the Backend, Next.js for the Frontend
    - _Requirements: 22.1, 22.2, 22.3, 22.4_

- [x] 12. Checkpoint - Implementation and tests complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 13. Build gates and secret-safe delivery
  - [x] 13.1 Docker build and full pytest
    - Run `docker build -t vericobol-backend ./backend`, then the full pytest suite inside the container; require 0 failures
    - Fix failures before continuing
    - _Requirements: 23.7, 24.1_

  - [x] 13.2 Frontend checks
    - In `frontend/`: `npm run check:nofloat`, `npx tsc --noEmit`, `npm run lint`, `npm run build`; each must exit 0
    - _Requirements: 16.6, 24.2_

  - [x] 13.3 Port 8001 smoke check
    - Start a temporary container on port 8001 (without reading `.env`; pass env vars explicitly or use `--env-file` only if the user approves), `GET /health`, post a `CALL "SYSTEM"` program and expect 400, confirm responses contain no environment content, then stop and remove the container
    - _Requirements: 14.2, 14.3, 24.3, 3.2_

  - [x] 13.4 Secret check, update checkboxes, commit and push
    - `git check-ignore .env` succeeds (do not read `.env`); `.env.example` holds placeholders only; `git diff --cached --name-only` does not list `.env`
    - Update completed required checkboxes in `tasks.md`
    - Stage changed files by explicit name, commit, then push with `GIT_TERMINAL_PROMPT="0"` and `GCM_INTERACTIVE="never"` using `git push origin main`
    - _Requirements: 25.1, 25.2, 25.3, 25.4, 25.5_

## Notes

- Tasks marked with `*` are optional property-test or test sub-tasks and are skipped in the required plan.
- Each task references requirements for traceability; property tests reference design Properties 1 to 19.
- Checkpoints ensure incremental validation.
- Latency and memory targets (4.5, 7.3, 7.10, 10.7) are checked in the Docker smoke run, not by automated tests.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "2.1", "3.1", "3.2", "8.1"] },
    { "id": 1, "tasks": ["1.3", "2.2", "3.3", "3.4", "8.2", "8.3", "8.4", "8.5"] },
    { "id": 2, "tasks": ["2.3", "4.1", "8.6"] },
    { "id": 3, "tasks": ["4.2", "5.1", "8.7"] },
    { "id": 4, "tasks": ["5.2", "6.1"] },
    { "id": 5, "tasks": ["6.2"] },
    { "id": 6, "tasks": ["6.3", "6.4"] },
    { "id": 7, "tasks": ["7"] },
    { "id": 8, "tasks": ["9.1", "10.1", "11.1"] },
    { "id": 9, "tasks": ["10.2"] },
    { "id": 10, "tasks": ["12"] },
    { "id": 11, "tasks": ["13.1", "13.2"] },
    { "id": 12, "tasks": ["13.3"] },
    { "id": 13, "tasks": ["13.4"] }
  ]
}
```
