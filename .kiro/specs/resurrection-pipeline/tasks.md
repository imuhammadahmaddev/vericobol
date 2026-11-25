# Implementation Plan: Resurrection Pipeline

## Overview

The design uses Python 3.12, so no language prompt is needed. The pipeline reuses the existing Verification_Engine (`run_verification`, `validate_source`, `CobolRunner`, `load_restricted_function`, `FLOAT_BUG_REGISTRY`, `DecimalJSONResponse`, `_VERIFY_LOCK`) and adds no new verification logic. Work proceeds bottom-up: cache, LLM client, contract and export template, self-healing loop, pipeline and preset bundles, API endpoints, MCP server, hooks and devcontainer. The final tasks sync the older spec's task list, rebuild the Docker image with a full pytest run, then commit and push non-interactively.

Constraints that apply to every task:
- All monetary math uses `decimal.Decimal`. No `float`, float literals or `math.pow` outside the existing `*_float_bug` functions.
- No `pandas`, `numpy`, `scipy`, `torch`. Use `pathlib.Path` for all paths.
- Tests use a deterministic LLM stub. No test makes a network call or spends tokens.
- Property tests use `hypothesis` with `database=None` and at least 100 iterations (fewer where each example runs live COBOL, matching existing test modules).
- Property tests that use the live COBOL binary share one compiled binary per module and batch cases.

## Tasks

- [x] 1. Implement the pipeline cache
  - [x] 1.1 Create `backend/app/pipeline_cache.py`
    - Implement `normalize_cobol`, `cache_key` (SHA-256 over normalized source plus a demo-flag byte) and the thread-safe `PipelineCache` LRU (`OrderedDict`, `move_to_end` on get, evict oldest when more than 64 entries)
    - Define the `CachedResult` dataclass
    - _Requirements: 5.1, 5.2, 5.9_

  - [ ]* 1.2 Write property test for cache key normalization
    - File: `backend/tests/test_pipeline_cache_properties.py`
    - **Property 20: Cache key normalization is idempotent and separates demo variants**
    - **Validates: Requirements 5.1, 5.2**

  - [ ]* 1.3 Write property test for LRU capacity and eviction order
    - File: `backend/tests/test_pipeline_cache_properties.py`
    - Compare against a reference `OrderedDict` model over insert/lookup sequences longer than 64
    - **Property 25: The cache never exceeds its capacity and always evicts least-recently-used first**
    - **Validates: Requirements 5.9**

- [x] 2. Implement the LLM client and budget tracker
  - [x] 2.1 Create `backend/app/llm_client.py`
    - Implement `LLMConfig`, `LLMKeyMissing`, `LLMCallError(stage, message)`, `BudgetExceeded`, `BudgetTracker` (module singleton, `can_spend`, `record`, `Decimal("2.00")` cap) and `LLMClient` with `from_env`, `require_key`, `extract_rules`, `generate_python`, `repair_python`
    - Read only `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`. Treat unset, empty or `.env.example` placeholder keys as missing (read placeholder values from `.env.example` once)
    - One private `_call(stage, prompt)`: 30 s timeout, 64 KiB response cap, `json.loads(..., parse_float=Decimal)`, normalize every failure to `LLMCallError` with no key and no raw provider payload in the message
    - Check `.env.example` holds placeholders for `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `FRONTEND_URL`, `NEXT_PUBLIC_API_URL`, `COBC_PATH`, and `openai` is pinned in `backend/requirements.txt`
    - _Requirements: 2.3, 6.1, 6.2, 6.3, 6.7, 6.9, 6.10, 6.11_

  - [ ]* 2.2 Write property test for the $2.00 budget cap
    - File: `backend/tests/test_llm_client_properties.py`
    - **Property 30: The cumulative LLM budget is enforced at exactly $2.00**
    - **Validates: Requirements 6.10**

  - [ ]* 2.3 Write property test for LLM failure sanitization
    - File: `backend/tests/test_llm_client_properties.py`
    - Stub the SDK transport to time out or return garbage; assert the error text excludes the key and raw response
    - **Property 28: LLM failures never leak secrets or raw provider output**
    - **Validates: Requirements 6.9**

- [x] 3. Define pipeline data models, export template and test stub
  - [x] 3.1 Create the data models in `backend/app/resurrection.py`
    - Add frozen dataclasses `FieldContract`, `ContractSchema`, `ParsedRules`, `ArchitectOutput`, `StageRecord`, `AttemptRecord`, `FinalReport`, `ResurrectResponse` (with `to_dict()` that keeps Decimals for `DecimalJSONResponse`) and `PipelineStageError(status, stage, body)`
    - Add `contract_from_preset(preset_id)` using the existing catalog and `contract_from_field_schema(...)`
    - Add a `build_dashboard_metadata` and `build_pic_mappings` step that takes names, bounds and steps only from the resolved `ContractSchema`, overriding any LLM-supplied values
    - _Requirements: 1.2, 1.5, 1.7, 2.3, 2.4, 7.2, 7.4, 7.8_

  - [x] 3.2 Create `backend/app/export_template.py`
    - Implement `render(verified_python_code, schema)` returning a self-contained FastAPI module source that embeds the code verbatim and exposes one `POST /calculate` route built from the schema, using string-typed Decimal inputs and outputs
    - Output must contain no `float` type, float literal or `math.pow`
    - _Requirements: 1.8, 1.9_

  - [ ]* 3.3 Write property test for export equivalence
    - File: `backend/tests/test_export_properties.py`
    - Compile the rendered module, call its calculation function and compare with `calculate` loaded through `load_restricted_function`
    - **Property 5: Export module embeds and matches the verified function**
    - **Validates: Requirements 1.8**

  - [x] 3.4 Add the deterministic LLM stub fixtures to `backend/tests/conftest.py`
    - `StubLLMClient` implementing `extract_rules`, `generate_python`, `repair_python` with scripted or randomized outputs and a call counter; fixtures that inject it and a fresh `PipelineCache`; a fixture that clears `LLM_API_KEY`
    - Make sure the stub never touches the network
    - _Requirements: 12.2_

- [x] 4. Implement the self-healing loop
  - [x] 4.1 Add `SelfHealingLoop` to `backend/app/resurrection.py`
    - `MAX_RETRIES = 2`; at most 3 `run_verification` calls with the identical COBOL source; first counterexample of a failed attempt goes to `repair_python`; stop and certify at parity 100.00; one `AttemptRecord` per attempt; failure after attempt 3 returns `status="failed"`, `certified=False` and the last attempted code
    - Let `VerificationError` propagate as `PipelineStageError` with no retry
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8_

  - [ ]* 4.2 Write property test for repair input
    - File: `backend/tests/test_healing_loop_properties.py`
    - **Property 11: Repair always uses the prior attempt's first counterexample**
    - **Validates: Requirements 3.1**

  - [ ]* 4.3 Write property test for the attempt cap
    - File: `backend/tests/test_healing_loop_properties.py`
    - **Property 12: The self-healing loop never exceeds 3 attempts**
    - **Validates: Requirements 3.2**

  - [ ]* 4.4 Write property test for source invariance across retries
    - File: `backend/tests/test_healing_loop_properties.py`
    - **Property 13: Every retry re-verifies against the same COBOL source**
    - **Validates: Requirements 3.3**

  - [ ]* 4.5 Write property test for early stop and certification
    - File: `backend/tests/test_healing_loop_properties.py`
    - **Property 14: Reaching 100.00 parity always stops the loop and certifies**
    - **Validates: Requirements 3.4, 4.6**

  - [ ]* 4.6 Write property test for audit trail shape
    - File: `backend/tests/test_healing_audit_properties.py`
    - **Property 15: Audit trail records are sequential and carry a counterexample exactly on failure**
    - **Validates: Requirements 3.5**

  - [ ]* 4.7 Write property test for healing exhaustion
    - File: `backend/tests/test_healing_audit_properties.py`
    - **Property 16: Exhausting retries always yields an uncertified, failed, HTTP-200 result carrying the last code**
    - **Validates: Requirements 3.6, 3.7**

  - [ ]* 4.8 Write property test for `VerificationError` handling
    - File: `backend/tests/test_healing_audit_properties.py`
    - **Property 17: A `VerificationError` always stops the loop without a retry**
    - **Validates: Requirements 3.8**

- [x] 5. Implement the pipeline orchestration, demo mode and preset bundles
  - [x] 5.1 Add `Pipeline` to `backend/app/resurrection.py`
    - `Pipeline.run(...)`: cache/bundle lookup for Presets (skipped on `force_live_llm`), Legacy_Parser and Cloud_Architect stages with `StageRecord` timing and `source`, Demo_Mode attempt 1 from `FLOAT_BUG_REGISTRY` (deterministic, wrapped to the `calculate` entry point) and repair to the `REGISTRY` Decimal implementation as attempt 2, `SelfHealingLoop` call, `export_template.render`, cache write after a certified live run
    - Cache and bundle hits set `cache_hit=true`, `llm_used=false`, make zero LLM calls, never read `LLM_API_KEY`, and always run fresh `run_verification`; a stale entry failing verification returns `certified=false`
    - Require a live LLM (and `require_key`) only for Custom_COBOL or `force_live_llm`; seed `PipelineCache` from `preset_bundles.json` at import
    - Generated code is only ever executed through `load_restricted_function`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.6, 3.8, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 6.4, 6.5, 6.6, 6.8_

  - [x] 5.2 Create `backend/app/bundle_builder.py`
    - Runnable as `python -m app.bundle_builder`; builds `backend/cobol_samples/preset_bundles.json` for each Preset and each `demonstrate_self_healing` value (`"false"`, `"true"`) from the Decimal `REGISTRY` sources, catalog metadata and `export_template.render`, with deterministic stage records (`source="cache"`)
    - Refuse to write an entry unless live `run_verification` returns parity 100.00
    - Reject any generated code or export containing `float`
    - _Requirements: 5.3, 1.9, 12.9_

  - [x] 5.3 Generate and commit `backend/cobol_samples/preset_bundles.json`
    - Run the builder inside the devcontainer or Docker image where `cobc` is available
    - Confirm all six entries are present and certified
    - _Requirements: 5.3, 8.2, 12.9_

  - [ ]* 5.4 Write property test for stage record shape
    - File: `backend/tests/test_pipeline_response_properties.py`
    - **Property 1: Stage record shape is always well-formed**
    - **Validates: Requirements 1.2**

  - [ ]* 5.5 Write property test for `num_cases` pass-through
    - File: `backend/tests/test_pipeline_response_properties.py`
    - **Property 2: `num_cases` is passed through unchanged**
    - **Validates: Requirements 1.4, 7.9**

  - [ ]* 5.6 Write property test for float-free generated code
    - File: `backend/tests/test_pipeline_response_properties.py`
    - Cover bundled, cached and stub-LLM code
    - **Property 3: Generated code and export are float-free and load under the restricted sandbox**
    - **Validates: Requirements 1.9**

  - [ ]* 5.7 Write property test for contract-derived metadata
    - File: `backend/tests/test_pipeline_response_properties.py`
    - Use an adversarial stub that invents fields and alters bounds
    - **Property 4: Dashboard metadata and PIC mappings reflect only the resolved contract**
    - **Validates: Requirements 1.7, 2.3, 2.4**

  - [ ]* 5.8 Write property test for demonstration mode
    - File: `backend/tests/test_pipeline_demo_cache_properties.py`
    - **Property 18: Demonstration mode always ends certified with a recorded repair**
    - **Validates: Requirements 4.4**

  - [ ]* 5.9 Write property test for cache and bundle hits skipping the LLM
    - File: `backend/tests/test_pipeline_demo_cache_properties.py`
    - **Property 21: Cache and bundle hits always skip the LLM and report the hit flags**
    - **Validates: Requirements 5.4**

  - [ ]* 5.10 Write property test for fresh verification on hits
    - File: `backend/tests/test_pipeline_demo_cache_properties.py`
    - Include a tampered-cache case that fails fresh verification and yields `certified=false`
    - **Property 22: Cache and bundle hits still run live verification and report its fresh result**
    - **Validates: Requirements 5.5, 5.6**

  - [ ]* 5.11 Write property test for `force_live_llm`
    - File: `backend/tests/test_pipeline_demo_cache_properties.py`
    - **Property 23: `force_live_llm` always bypasses cache and bundle**
    - **Validates: Requirements 5.7**

  - [ ]* 5.12 Write property test for cache write-back
    - File: `backend/tests/test_pipeline_demo_cache_properties.py`
    - **Property 24: A certifying live run is always retrievable from the cache afterward**
    - **Validates: Requirements 5.8**

  - [ ]* 5.13 Write property test for no key read on hits
    - File: `backend/tests/test_pipeline_llm_trust_properties.py`
    - Patch `os.environ` access to record reads of `LLM_API_KEY`
    - **Property 27: A bundle/cache hit never reads `LLM_API_KEY`**
    - **Validates: Requirements 6.5**

  - [ ]* 5.14 Write property test for untrusted LLM code
    - File: `backend/tests/test_pipeline_llm_trust_properties.py`
    - Stub returns code with `__import__`, `eval`, `open`
    - **Property 29: Untrusted LLM-generated code is always mediated by the restricted loader**
    - **Validates: Requirements 6.8**

- [x] 6. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Add the resurrect and export endpoints
  - [x] 7.1 Extend `backend/app/main.py`
    - Add `ResurrectRequest` (reuse `MoneyValue`/`_money`/`_JsonFloat`; `field_schema` required with `cobol_code` and each field needs name, PIC and direction; exactly one of `preset_id`/`cobol_code`; `num_cases` in 150..200; `demonstrate_self_healing`; `force_live_llm`)
    - Add `POST /api/resurrect`: 256 KiB limit (413), 404 with valid ids for unknown preset, 4xx for demo mode with Custom_COBOL, `validate_source` before any LLM call, `Pipeline.run` in the threadpool under the shared `_VERIFY_LOCK`, `DecimalJSONResponse` output, error mapping (`PipelineStageError`, `LLMKeyMissing` and `BudgetExceeded` to 503, `LLMCallError` to 502 with stage name)
    - Add `GET /api/export/{preset_id}` served only from `preset_bundles.json` (404 with valid ids), with no LLM or compiler access
    - Keep `GET /health` free of the LLM client, compiler and cache
    - _Requirements: 2.1, 2.2, 2.5, 2.6, 2.7, 2.8, 2.9, 4.5, 5.10, 6.4, 6.6, 6.9, 6.10, 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 8.1, 8.2, 8.3, 8.4_

  - [ ]* 7.2 Write property test for missing or invalid `field_schema`
    - File: `backend/tests/test_resurrect_validation_properties.py`
    - **Property 6: Missing or invalid `field_schema` is always rejected before the sandbox and LLM run**
    - **Validates: Requirements 2.1, 2.2**

  - [ ]* 7.3 Write property test for unknown presets
    - File: `backend/tests/test_resurrect_validation_properties.py`
    - **Property 7: Unknown preset identifiers are always rejected with the valid list**
    - **Validates: Requirements 2.6**

  - [ ]* 7.4 Write property test for `num_cases` range
    - File: `backend/tests/test_resurrect_validation_properties.py`
    - **Property 8: `num_cases` outside [150, 200] is always rejected**
    - **Validates: Requirements 2.7**

  - [ ]* 7.5 Write property test for float-literal bounds
    - File: `backend/tests/test_resurrect_validation_properties.py`
    - **Property 9: Float-literal monetary bounds are always rejected**
    - **Validates: Requirements 2.8, 7.5**

  - [ ]* 7.6 Write property test for sandbox rejection before LLM
    - File: `backend/tests/test_resurrect_policy_properties.py`
    - **Property 10: Sandbox rejection always precedes and prevents any LLM call**
    - **Validates: Requirements 2.9**

  - [ ]* 7.7 Write property test for demo mode with Custom_COBOL
    - File: `backend/tests/test_resurrect_policy_properties.py`
    - **Property 19: Demonstration mode is rejected for Custom_COBOL**
    - **Validates: Requirements 4.5**

  - [ ]* 7.8 Write property test for missing LLM key
    - File: `backend/tests/test_resurrect_policy_properties.py`
    - Cover unset, empty, whitespace and placeholder keys
    - **Property 26: `LLM_Key_Missing` always yields HTTP 503 when a live call is required**
    - **Validates: Requirements 6.4, 6.6**

  - [ ]* 7.9 Write property test for monetary values as JSON strings
    - File: `backend/tests/test_resurrect_response_properties.py`
    - **Property 31: Every monetary value in the response is a JSON string**
    - **Validates: Requirements 7.4**

  - [ ]* 7.10 Write property test for serialized verification
    - File: `backend/tests/test_resurrect_response_properties.py`
    - Use an instrumented runner recording enter/exit times across concurrent `/api/resurrect` and `/api/verify` requests
    - **Property 32: Concurrent verification runs never overlap**
    - **Validates: Requirements 7.7**

  - [x]* 7.11 Write endpoint example tests in `backend/tests/test_resurrection_pipeline.py`
    - Each Preset: `cache_hit` true, `llm_used` false, `certified` true, all 11 response keys, `preset_id` null for Custom_COBOL
    - Demo_Mode: failed attempt 1 with a Counterexample, passing attempt 2, final score 100.00
    - Healing failure: HTTP 200, 3 attempt records, `status` failed, `certified` false
    - 4xx for missing `field_schema`, both/neither of `preset_id`/`cobol_code`, 413 oversized body, 503 missing key, 502 LLM failure, 503 budget exhausted, cache key rule, `GET /api/export` happy path and 404
    - `GET /health` and `GET /api/export` with the LLM client and compiler patched to raise
    - No `float` in `verified_python_code` and `export_fastapi_code` of any bundled result; every bundle passes live verification at 100.00
    - _Requirements: 1.5, 1.6, 2.1, 3.6, 4.4, 5.10, 6.4, 6.9, 6.10, 7.2, 7.3, 7.6, 7.8, 8.2, 8.3, 8.4, 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.8, 12.9_

- [x] 8. Implement the MCP server
  - [x] 8.1 Create `backend/mcp_server/__init__.py` and `backend/mcp_server/server.py`
    - Stdlib-only newline-delimited JSON-RPC 2.0 over stdio: `initialize` (server name `vericobol-gnucobol`, `tools` capability), `tools/list` (exactly `list_cobol_presets`, `compile_and_run_cobol`, `verify_cobol_parity`, each with a JSON schema), `tools/call`
    - `compile_and_run_cobol` runs `validate_source` then `CobolRunner` in a fresh temp directory; `verify_cobol_parity` calls `run_verification`; sandbox rejections return `isError` true
    - Unknown method returns -32601; invalid JSON returns -32700 and the loop continues; stdout carries only JSON-RPC, diagnostics go to stderr
    - Import `app.*` so it runs as `python -m mcp_server.server` from the image `WORKDIR`, with a `sys.path` fallback for running from the repository root
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8, 9.9, 9.10, 9.11_

  - [x] 8.2 Create `.kiro/settings/mcp.json`
    - Server `vericobol-gnucobol`, command `docker` with args `run -i --rm vericobol-backend python -m mcp_server.server`; no `env` block, no secrets
    - _Requirements: 9.12, 9.13_

  - [x]* 8.3 Write MCP example tests in `backend/tests/test_mcp_server.py`
    - `initialize`, `tools/list`, one happy-path call per tool, sandbox-rejected source with `isError`, unknown method (-32601), malformed JSON (-32700, loop continues), stdout purity
    - _Requirements: 9.3, 9.4, 9.5, 9.6, 9.7, 9.8, 9.9, 9.10, 9.11, 12.1, 12.7_

  - [ ]* 8.4 Write property test for MCP and direct runner equivalence
    - File: `backend/tests/test_mcp_properties.py`
    - **Property 33: The MCP `compile_and_run_cobol` tool matches direct `CobolRunner` use**
    - **Validates: Requirements 9.6, 9.7**

- [x] 9. Add hooks, devcontainer and image contents
  - [x] 9.1 Create the float guard script and the two Kiro hooks
    - `backend/tools/guard_no_float.py`: stdlib AST scanner reporting `float(`, float literals and `math.pow` with line numbers, taking the file path as an argument
    - `.kiro/hooks/guard-no-float.kiro.hook`: save trigger for Python files under `backend/app/` and `backend/cobol_samples/`
    - `.kiro/hooks/verify-cobol-parity.kiro.hook`: save trigger for `.cbl` files under `backend/cobol_samples/`, running the three Preset property test files
    - Both files valid JSON, argument-list commands, no secrets
    - _Requirements: 10.1, 10.2, 10.3, 10.4_

  - [x] 9.2 Create `.devcontainer/devcontainer.json`
    - Build from `../backend/Dockerfile` with context `../backend`, `COBC_PATH=cobc`, `forwardPorts: [8000]`, `postCreateCommand` installing `backend/requirements.txt`, no secrets
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5_

  - [x] 9.3 Update `backend/Dockerfile` and `backend/.dockerignore`
    - Ensure the image still uses `python:3.12-slim` with GnuCOBOL, and contains `mcp_server/`, `tools/` and `cobol_samples/preset_bundles.json`, while excluding `.hypothesis/` and `.env`
    - No keys or endpoints in the Dockerfile
    - _Requirements: 6.2, 13.3, 13.4_

  - [ ]* 9.4 Write static configuration tests in `backend/tests/test_static_config.py`
    - Hook files and `mcp.json` parse as JSON and contain no secret-like values; devcontainer references `backend/Dockerfile`, forwards 8000, sets `COBC_PATH`; `.env.example` has the six placeholder keys; `requirements.txt` has no banned ML packages; no hardcoded keys or endpoints in `backend/`
    - _Requirements: 6.2, 6.3, 6.11, 9.13, 10.1, 10.4, 11.1, 11.2, 11.4, 11.5_

- [x] 10. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Sync `.kiro/specs/cobol-verification-engine/tasks.md`
  - Append a section for the resurrection-pipeline extension to `.kiro/specs/cobol-verification-engine/tasks.md` that lists the new top-level tasks (1 to 10 above) as checkboxes with their completed state, in the same format as the existing file, and point to `.kiro/specs/resurrection-pipeline/tasks.md`
  - Add the new task ids to that file's `## Task Dependency Graph` JSON as additional waves after the existing ones; keep the JSON valid and keep `tasks.meta.json` consistent if it tracks task ids
  - Run `validate_spec_format` on the edited file and fix any errors
  - _Requirements: 14.1_

- [x] 12. Rebuild the Docker image and run the full test suite
  - From the repository root run `docker build -t vericobol-backend ./backend` and require exit code 0
  - Run the whole suite inside the image, for example `docker run --rm vericobol-backend python -m pytest -q`, and require 0 failures and 0 errors
  - If either step fails, fix the cause and rerun both steps before moving on
  - Confirm the image still serves `GET /health` and contains `mcp_server/` and `cobol_samples/preset_bundles.json`
  - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5_

- [x] 13. Commit all changes and push non-interactively
  - In PowerShell set `$env:GIT_TERMINAL_PROMPT="0"; $env:GCM_INTERACTIVE="never"` in the same command as the push
  - Run `git status`, stage everything with `git add -A` (the existing `.gitignore` must exclude `.env` and `.hypothesis/`; verify neither appears in `git status` and `.kiro/` is included), then `git commit` with a descriptive message
  - Run `git push` to the current branch. Never use force push. If the push fails, report the git error output to the user
  - After a successful push confirm `git rev-parse HEAD` equals the remote branch head from `git ls-remote origin <branch>`
  - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5_

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP.
- Each task references specific requirements for traceability.
- Property tests are split across several files so tasks that write different files can run in parallel.
- Property tests validate the 33 correctness properties in `design.md`; example tests cover the remaining acceptance criteria.
- Tasks 11 to 13 are release steps requested for this spec. They run shell and git commands and are not coding tasks.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1", "3.1"] },
    { "id": 1, "tasks": ["1.2", "2.2", "3.2", "3.4", "4.1"] },
    { "id": 2, "tasks": ["1.3", "2.3", "3.3", "4.2", "4.6", "5.1"] },
    { "id": 3, "tasks": ["4.3", "4.7", "5.2"] },
    { "id": 4, "tasks": ["4.4", "4.8", "5.3"] },
    { "id": 5, "tasks": ["4.5", "5.4", "5.8", "5.13"] },
    { "id": 6, "tasks": ["5.5", "5.9", "5.14"] },
    { "id": 7, "tasks": ["5.6", "5.10"] },
    { "id": 8, "tasks": ["5.7", "5.11"] },
    { "id": 9, "tasks": ["5.12"] },
    { "id": 10, "tasks": ["6"] },
    { "id": 11, "tasks": ["7.1", "8.1", "8.2", "9.1", "9.2", "9.3"] },
    { "id": 12, "tasks": ["7.2", "7.6", "7.9", "7.11", "8.3", "8.4", "9.4"] },
    { "id": 13, "tasks": ["7.3", "7.7", "7.10"] },
    { "id": 14, "tasks": ["7.4", "7.8"] },
    { "id": 15, "tasks": ["7.5"] },
    { "id": 16, "tasks": ["10"] },
    { "id": 17, "tasks": ["11"] },
    { "id": 18, "tasks": ["12"] },
    { "id": 19, "tasks": ["13"] }
  ]
}
```
