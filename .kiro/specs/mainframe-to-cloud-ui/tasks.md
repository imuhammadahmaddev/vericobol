# Implementation Plan: Mainframe-to-Cloud UI

## Overview

Implement the Next.js (App Router) + TypeScript + Tailwind frontend in `frontend/`. Work proceeds bottom-up: scaffold, then the pure libraries (`types.ts`, `money.ts`, `api.ts`), then hooks, then presentational components, then page wiring. Verification (float guard, `tsc`, lint, build, backend smoke check) and the commit/push come last. Language: TypeScript. Test tooling: Vitest, React Testing Library and `fast-check` as dev dependencies only (no new runtime dependencies beyond Next.js and React, per Requirement 1.4).

Design resolutions to honor during implementation: the "0 floats verified" badge is derived from `final_report.certified`; the Parity_Banner cache note is derived from `cache_hit`; error `field` is read from `detail.field`; attempt outcome is derived by a pure helper `deriveAttemptOutcome`; the wire key for verify/resurrect errors is `error`, not `kind`.

## Tasks

- [x] 1. Scaffold the frontend project
  - [x] 1.1 Create the Next.js App Router project in `frontend/`
    - Add `package.json`, `tsconfig.json` (strict), `next.config.mjs`, Tailwind and PostCSS config, ESLint config, `src/app/layout.tsx`, `src/app/globals.css`, and a placeholder `src/app/page.tsx`
    - Runtime dependencies limited to `next`, `react`, `react-dom`
    - Add `frontend/.env.local.example` with a placeholder `NEXT_PUBLIC_API_URL` and no secrets
    - Confirm `.gitignore` excludes `node_modules`, `.next`, `.env.local`
    - Define the 3270 theme tokens (`#0A0E0C`, `#00FF66`) in Tailwind config
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 17.2_

  - [x] 1.2 Configure the test tooling
    - Add Vitest, React Testing Library, jsdom, and `fast-check` as dev dependencies, plus `vitest.config.ts` and a setup file
    - Add `test` script (`vitest --run`)
    - _Requirements: 1.4_

- [x] 2. Implement the typed API client and money helpers
  - [x] 2.1 Create `src/lib/types.ts`
    - Declare every type from the design: `HealthResponse`, `PresetSummary`, `CalculateResponse`, `Counterexample`, `LikelyCause`, `ParityReport`, `StageRecord`, `AttemptRecord`, `FinalReport`, `PicMapping`, `DashboardMetadata`, `ResurrectResponse`, `ExportResponse`, verify request/response, `ResurrectRequestBody`, `AttemptOutcome`, error detail types, and `ApiError`
    - Type all monetary, rate and numeric result fields as `MoneyString`
    - Comment that the wire key is `error`, not `kind`, and that `penny_delta` may be `"n/a"`
    - Declare error bodies for statuses 400, 404, 413, 422, 500, 502, 503, 504
    - _Requirements: 2.2, 2.3, 14.2, 14.3_

  - [x] 2.2 Create `src/lib/money.ts`
    - Implement `stepIndexToMoneyString`, `moneyStringToStepIndex`, `clampToSchema`, `validateDecimal`, `compareDecimalStrings` using string and BigInt arithmetic only
    - Add a `formatMoneyString` helper that inserts thousands separators by string operations
    - _Requirements: 3.1, 3.3, 3.4, 3.5, 9.2, 9.6_

  - [ ]* 2.3 Write property test for step-index round trip
    - **Property 3: Step-index/Money-String round trip is exact**
    - **Validates: Requirements 3.4**

  - [ ]* 2.4 Write property test for schema clamping
    - **Property 9: Schema clamping always yields an in-range, step-aligned value**
    - **Validates: Requirements 9.2, 10.4**

  - [ ]* 2.5 Write property test for validation acceptance
    - **Property 11: Validation acceptance is equivalent to parse-and-range success**
    - **Validates: Requirements 9.6**

  - [x] 2.6 Create `src/lib/api.ts` and `src/lib/sandboxSnippet.ts`
    - Implement the `request<T>` helper as the only `fetch` caller; read `NEXT_PUBLIC_API_URL` with fallback `http://localhost:8000`
    - Send `Content-Type: application/json`; parse with `JSON.parse` without numeric coercion; throw `ApiError(status, detail)` on non-2xx
    - Expose `health`, `presets`, `calculate`, `resurrect`, `verify`, `exportPreset` with optional `AbortSignal`
    - Add the `Sandbox_Test_Snippet` constant containing `CALL "SYSTEM"`
    - _Requirements: 1.2, 1.5, 2.1, 2.4, 2.5, 2.6, 3.2, 8.5_

  - [ ]* 2.7 Write property test for decimal-string passthrough
    - **Property 1: Decimal-string passthrough on parse**
    - **Validates: Requirements 2.6**

  - [ ]* 2.8 Write property test for outgoing money serialization
    - **Property 2: Outgoing money values are always quoted JSON strings**
    - **Validates: Requirements 3.2**

- [x] 3. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Implement hooks and leaf components
  - [x] 4.1 Implement `src/hooks/useHealth.ts`
    - Call `GET /health` on mount, retry every 3000 ms until `status === "ok"`, cancel timers and abort requests on unmount
    - _Requirements: 4.1, 4.3, 4.7_

  - [x] 4.2 Implement `src/hooks/useResurrect.ts`
    - Expose `run(body)`, `loading`, `data`, `error`; tag each call with an increasing request id and abort the previous call; discard stale responses
    - _Requirements: 6.3, 6.7_

  - [x] 4.3 Implement `src/hooks/useCalculate.ts`
    - Debounce 250 ms with cancel-and-restart; apply the same latest-wins guard
    - _Requirements: 9.3, 9.4, 9.8_

  - [ ]* 4.4 Write property test for latest-wins sequencing
    - **Property 5: Latest-wins request sequencing**
    - **Validates: Requirements 6.7, 9.8**

  - [ ]* 4.5 Write property test for debounce coalescing
    - **Property 10: Change-event debounce coalesces to exactly one trigger**
    - **Validates: Requirements 9.3, 9.4**

  - [x] 4.6 Implement `HealthPill.tsx` and `ColdStartBanner.tsx`
    - Pill shows online only when `status === "ok"`, "GnuCOBOL unavailable" when `cobc_available === false`, and `cobc_version` only when non-empty
    - Banner text: "Establishing 3270 Mainframe Connection... Warming up GnuCOBOL compiler..."
    - _Requirements: 4.2, 4.4, 4.5, 4.6_

  - [ ]* 4.7 Write property test for Health_Pill state
    - **Property 4: Health_Pill state is a pure function of the health response**
    - **Validates: Requirements 4.4, 4.5, 4.6**

  - [x] 4.8 Implement `PipelineStepper.tsx`
    - Always render the three fixed stages in order; show `elapsed_ms` and a source badge verbatim; "not run" for absent stages; pending state while loading
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

  - [ ]* 4.9 Write property test for Pipeline_Stepper
    - **Property 6: Pipeline_Stepper is a total, order-preserving function of `stages`**
    - **Validates: Requirements 7.1, 7.2, 7.3, 7.5**

  - [x] 4.10 Implement `PresetTabs.tsx`
    - One tab per preset using the backend `name`; error message naming `GET /api/presets` with a retry control on failure
    - _Requirements: 5.1, 5.3, 5.4_

  - [ ]* 4.11 Write unit tests for health and preset UI
    - Cover ok, degraded and unreachable health states, and preset tab rendering and the retry control
    - _Requirements: 4.2, 4.4, 4.5, 5.1, 5.4_

- [x] 5. Implement the Legacy Mainframe panel
  - [x] 5.1 Implement `CobolSourceEditor.tsx` and `RawStdoutView.tsx`
    - 3270 styling; editable source; control that loads the Sandbox_Test_Snippet; show `raw_cobol_output` as live STDOUT
    - Track edited source so the next Resurrect_Action sends `cobol_code` with the matching `field_schema`
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

  - [x] 5.2 Implement `RejectionDetail.tsx`
    - Narrow `detail` to `RejectedErrorDetail` only when status is 400 and `error === "rejected"`; display status 400, `token`, `line`, `message`
    - _Requirements: 8.6_

  - [ ]* 5.3 Write property test for rejection rendering
    - **Property 7: Rejection detail rendering is complete and verbatim**
    - **Validates: Requirements 8.6**

  - [x] 5.4 Implement `BusinessRulesAndPicMap.tsx`
    - List `business_rules`; render one PIC map row per `pic_mappings` entry with name, PIC, scale and rounding verbatim
    - _Requirements: 8.7_

  - [ ]* 5.5 Write property test for PIC map rendering
    - **Property 8: PIC map rendering is a lossless one-row-per-entry mapping**
    - **Validates: Requirements 8.7**

  - [x] 5.6 Implement `LegacyPanel.tsx`
    - Compose editor, stdout, rejection detail, and rules/PIC map
    - _Requirements: 8.1, 8.2, 8.4, 8.6, 8.7_

  - [ ]* 5.7 Write unit tests for the editor
    - Verify the snippet loader and that edited source is exposed as `cobol_code`
    - _Requirements: 8.3, 8.5_

- [x] 6. Implement the Cloud Dashboard
  - [x] 6.1 Implement `FieldSliderInput.tsx`
    - Slider driven by integer step index plus a decimal text input bound to one `MoneyString`; constrain to schema `min`, `max`, `step`; inline validation message; per-field server error slot
    - _Requirements: 3.1, 3.4, 9.1, 9.2, 9.6, 9.7_

  - [ ]* 6.2 Write property test for 422 field-error rendering
    - **Property 12: 422 field-error rendering targets the named field**
    - **Validates: Requirements 9.7**

  - [x] 6.3 Implement chart geometry helpers and the three SVG charts
    - Create `src/lib/chartGeometry.ts` with BigInt proportional-width functions; implement `LoanChart.tsx`, `CompoundChart.tsx`, `LedgerChart.tsx` (overdraft indicator driven by `overdraft_flag`)
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5_

  - [ ]* 6.4 Write property test for chart geometry
    - **Property 13: Per-preset chart geometry is proportional, reconstructive, and float-free**
    - **Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.5**

  - [x] 6.5 Implement `VerifiedPythonTab.tsx`
    - Show `verified_python_code`; show the "0 floats verified" badge when `final_report.certified` is true; copy-to-clipboard with confirmation; download as `{preset_id}.py`
    - _Requirements: 11.1, 11.2, 11.3, 11.4_

  - [x] 6.6 Implement `ExportTab.tsx`
    - Fetch `GET /api/export/{preset_id}` when opened; show `export_fastapi_code`; download as `{preset_id}_api.py`; show the error code on non-2xx
    - _Requirements: 11.5, 11.6, 11.7_

  - [ ]* 6.7 Write unit tests for copy and download controls
    - Mock the clipboard and download APIs; assert exact content and filenames
    - _Requirements: 11.3, 11.4, 11.6, 11.7_

  - [x] 6.8 Implement `CloudDashboard.tsx`
    - Compose inputs, results (formatted `MoneyString`), the active preset's chart, and the Verified Python and Export tabs; use `useCalculate`; render pydantic-array 422 errors as a bulleted list
    - _Requirements: 9.1, 9.3, 9.5, 9.7, 10.5, 11.1, 11.5_

- [x] 7. Implement the parity banner and audit trail
  - [x] 7.1 Implement `ParityBanner.tsx`
    - Full-width; text `"{passed_cases} / {total_cases} Exact Penny Matches"`; "0 Hallucinations" and passing state iff `failed_cases === 0`, otherwise failing state with the mismatch count; when `cache_hit` is true, state that live GnuCOBOL verification ran on this request
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5_

  - [ ]* 7.2 Write property test for Parity_Banner
    - **Property 14: Parity_Banner text and visual state are a pure function of the report counts**
    - **Validates: Requirements 12.2, 12.3, 12.4**

  - [x] 7.3 Implement `AuditTrail.tsx` and `deriveAttemptOutcome`
    - One entry per attempt in order with attempt number and derived outcome; counterexample diff table of inputs, COBOL output, Python output, differing field, likely cause, all as raw strings; empty state text "No discrepancies found; no repair needed"
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5_

  - [ ]* 7.4 Write property test for Audit_Trail
    - **Property 15: Audit_Trail rendering is lossless and verbatim, including the empty case**
    - **Validates: Requirements 13.1, 13.2, 13.3, 13.5**

- [x] 8. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Wire the page together
  - [x] 9.1 Implement `src/app/page.tsx` and finalize `layout.tsx`
    - Split-screen layout: Legacy_Panel left, Cloud_Dashboard right, Parity_Banner and Audit_Trail full width below; header with Health_Pill; Cold_Start_Banner while health is not ok
    - On first successful health, load presets, select `loan_calc` and run Resurrect_Action automatically; preset tab change loads source and schema and re-runs
    - Resurrect and Demo buttons (`num_cases` 150; Demo adds `demonstrate_self_healing: true`), disabled while loading; send `cobol_code` and `field_schema` when the source was edited
    - Display error code and message on failure; for 503 add the note that bundled presets need no LLM key
    - _Requirements: 4.1, 4.2, 4.4, 5.2, 5.3, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 8.3, 12.1_

  - [ ]* 9.2 Write unit tests for page wiring
    - Mock the API client; assert auto-resurrect of `loan_calc`, request shapes for Resurrect and Demo, disabled buttons while loading, and the 503 message
    - _Requirements: 5.2, 6.1, 6.2, 6.3, 6.6_

- [x] 10. Build, lint, type-check, and smoke verification
  - [x] 10.1 Add the no-float guard script
    - Add `frontend/scripts/check-no-float.mjs` and an npm script `check:nofloat` that searches `frontend/src/` for `parseFloat`, `Number(` and `toFixed` on money values and exits non-zero on any match
    - _Requirements: 3.5, 15.4_

  - [x] 10.2 Add the backend smoke-check script
    - Add `frontend/scripts/smoke.mjs` (or `backend/tools/smoke_check.py`) that checks `GET /health` returns 200 with `status` `ok`; `POST /api/resurrect` for `loan_calc` with `num_cases` 150 returns 200 with zero mismatches; the Sandbox_Test_Snippet returns 400 with `error` `rejected`; `GET /api/export/loan_calc` returns 200 with non-empty `export_fastapi_code`
    - Accept a base URL argument; report when Docker was skipped and a local Uvicorn was used
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5_

  - [x] 10.3 Run `npm run check:nofloat`, `npx tsc --noEmit`, `npm run lint`, `npm run build`, and `npm run test` in `frontend/`
    - Fix any failure; all must exit 0 with zero guard matches
    - _Requirements: 14.1, 15.1, 15.2, 15.3, 15.4_

  - [x] 10.4 Run the smoke check against the backend
    - Build and run the Docker image from `backend/Dockerfile`; if Docker is unavailable, start `uvicorn app.main:app` locally from `backend/` and note that Docker was skipped; run the smoke script and confirm all four checks pass; stop any started process afterwards
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5_

- [x] 11. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 12. Commit and push
  - Stage only `.kiro/specs/mainframe-to-cloud-ui/` and `frontend/` by explicit path; confirm `node_modules`, `.next`, `.env.local` are not staged
  - Create a single commit
  - Run `$env:GIT_TERMINAL_PROMPT="0"; $env:GCM_INTERACTIVE="never"; git push origin main`
  - If the push fails, report the git error output and leave the local commit intact
  - _Requirements: 17.1, 17.2, 17.3, 17.4_

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties (`fast-check`, at least 100 runs, tagged `Feature: mainframe-to-cloud-ui, Property N: <title>`)
- Unit tests validate specific examples and edge cases
- Tasks 10.3 onward must run only after all implementation tasks are complete; task 12 runs only if every verification passes

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2"] },
    { "id": 2, "tasks": ["2.1", "2.2"] },
    { "id": 3, "tasks": ["2.3", "2.4", "2.5", "2.6"] },
    { "id": 4, "tasks": ["2.7", "2.8"] },
    { "id": 5, "tasks": ["3"] },
    { "id": 6, "tasks": ["4.1", "4.2", "4.3", "4.6", "4.8", "4.10", "5.1", "5.2", "5.4", "6.1", "6.3", "6.5", "6.6", "7.1", "7.3"] },
    { "id": 7, "tasks": ["4.4", "4.5", "4.7", "4.9", "4.11", "5.3", "5.5", "5.7", "6.2", "6.4", "6.7", "7.2", "7.4"] },
    { "id": 8, "tasks": ["5.6", "6.8"] },
    { "id": 9, "tasks": ["8"] },
    { "id": 10, "tasks": ["9.1"] },
    { "id": 11, "tasks": ["9.2", "10.1", "10.2"] },
    { "id": 12, "tasks": ["10.3", "10.4"] },
    { "id": 13, "tasks": ["11"] },
    { "id": 14, "tasks": ["12"] }
  ]
}
```
