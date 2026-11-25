# Requirements Document

## Introduction

The Mainframe_UI is the Next.js (App Router), TypeScript and Tailwind CSS frontend in `frontend/` for VeriCOBOL. It presents a split-screen experience: a 3270-style "Legacy Mainframe" panel on the left, a modern "Resurrected Cloud Dashboard" on the right, and a full-width "Mathematical Parity Proof" banner and self-healing audit trail below. It consumes the existing Backend_API (`backend/app/main.py`, `backend/app/resurrection.py`) through a single typed API_Client. All money values travel as strings, charts are native SVG, and no heavy charting or state libraries are added. Delivery includes build, lint and type-check verification, a backend smoke check, and a commit pushed to `origin main`.

## Glossary

- **Mainframe_UI**: The Next.js frontend application in `frontend/`.
- **Backend_API**: The FastAPI service defined in `backend/app/main.py` and `backend/app/resurrection.py`.
- **API_Client**: The single typed module in `frontend/src/lib/` through which the Mainframe_UI issues every HTTP request to the Backend_API.
- **Types_Module**: `frontend/src/lib/types.ts`, holding TypeScript types for all Backend_API request and response bodies.
- **Money_String**: A monetary or rate value represented as a JavaScript string holding a decimal number, never a JavaScript number.
- **Preset**: One of `loan_calc`, `compound_int`, `daily_ledger`, as served by `GET /api/presets`.
- **Health_Pill**: The header status indicator that reflects the result of `GET /health`.
- **Cold_Start_Banner**: A themed banner reading "Establishing 3270 Mainframe Connection... Warming up GnuCOBOL compiler..." shown while the Backend_API is unreachable.
- **Pipeline_Stepper**: The three-step display of Legacy Parser, Cloud Architect and Parity Auditor stages.
- **Legacy_Panel**: The left 3270-style panel with background `#0A0E0C` and phosphor-green `#00FF66` monospace text.
- **Cloud_Dashboard**: The right panel containing inputs, sliders, SVG visuals and code tabs.
- **Parity_Banner**: The full-width panel showing the live differential PBT result.
- **Audit_Trail**: The section listing self-healing attempts with counterexample diff tables.
- **Resurrect_Action**: The "Resurrect & Verify" button, which calls `POST /api/resurrect`.
- **Demo_Action**: The "Demo float-bug self-heal" button, which calls `POST /api/resurrect` with `demonstrate_self_healing` set to true.
- **Sandbox_Test_Snippet**: A COBOL snippet containing `CALL "SYSTEM"` that the user can submit to demonstrate sandbox rejection.
- **Export_Tab**: The Cloud_Dashboard tab showing FastAPI export code from `GET /api/export/{preset_id}`.

## Requirements

### Requirement 1: Project scaffold and configuration

**User Story:** As a developer, I want a standard Next.js TypeScript project in `frontend/`, so that the UI builds and deploys to Vercel.

#### Acceptance Criteria

1. THE Mainframe_UI SHALL reside in `frontend/` as a Next.js App Router project written in TypeScript and styled with Tailwind CSS.
2. THE Mainframe_UI SHALL read the Backend_API base URL only from the `NEXT_PUBLIC_API_URL` environment variable.
3. THE Mainframe_UI SHALL provide `frontend/.env.local.example` containing a placeholder `NEXT_PUBLIC_API_URL` and no secrets.
4. THE Mainframe_UI SHALL declare no charting, state-management or UI-component runtime dependencies beyond Next.js, React and Tailwind CSS tooling.
5. IF `NEXT_PUBLIC_API_URL` is not set, THEN THE API_Client SHALL use `http://localhost:8000` as the base URL.

### Requirement 2: Typed API client and contract fidelity

**User Story:** As a developer, I want one typed API client that matches the backend, so that contract drift is caught at compile time.

#### Acceptance Criteria

1. THE API_Client SHALL be the only module in the Mainframe_UI that calls `fetch`.
2. THE Types_Module SHALL define request and response types for `GET /health`, `GET /api/presets`, `POST /api/calculate`, `POST /api/resurrect`, `POST /api/verify` and `GET /api/export/{preset_id}` matching the field names and optionality in `backend/app/main.py` and `backend/app/resurrection.py`.
3. THE Types_Module SHALL type every monetary, rate and numeric result field as a Money_String.
4. WHEN the Backend_API returns a non-2xx status, THE API_Client SHALL throw a typed error carrying the HTTP status and the parsed `detail` or error body.
5. THE API_Client SHALL send JSON request bodies using the `Content-Type: application/json` header.
6. THE API_Client SHALL parse response bodies without converting decimal strings to JavaScript numbers.

### Requirement 3: String-only money handling

**User Story:** As a compliance reviewer, I want no floating-point money in the UI, so that displayed values are penny-exact.

#### Acceptance Criteria

1. THE Mainframe_UI SHALL hold every monetary and rate input value in state as a Money_String.
2. WHEN the Mainframe_UI sends a monetary or rate value to the Backend_API, THE API_Client SHALL serialize the value as a JSON string.
3. THE Mainframe_UI SHALL render monetary result values by formatting the Money_String with string operations only.
4. WHEN a slider value changes, THE Mainframe_UI SHALL derive the Money_String from the slider's integer step index using integer arithmetic and the preset field scale.
5. THE Mainframe_UI SHALL contain no call to `parseFloat`, `Number()` or `toFixed` on monetary or rate values.

### Requirement 4: Header and health status

**User Story:** As a visitor, I want to see backend readiness, so that I know when the cold-started service is usable.

#### Acceptance Criteria

1. WHEN the page loads, THE Mainframe_UI SHALL send `GET /health` through the API_Client without user action.
2. WHILE `GET /health` has not returned a successful response, THE Mainframe_UI SHALL display the Cold_Start_Banner.
3. WHILE `GET /health` has not returned a successful response, THE Mainframe_UI SHALL retry `GET /health` every 3 seconds.
4. WHEN `GET /health` returns `status` equal to `ok`, THE Health_Pill SHALL show an online state and THE Mainframe_UI SHALL hide the Cold_Start_Banner.
5. WHEN `GET /health` returns `cobc_available` equal to false, THE Health_Pill SHALL show a degraded state containing the text "GnuCOBOL unavailable".
6. WHERE `cobc_version` is non-empty, THE Health_Pill SHALL display the `cobc_version` value.
7. WHEN the Mainframe_UI is unmounted, THE Mainframe_UI SHALL cancel pending health retries.

### Requirement 5: Preset selection

**User Story:** As a visitor, I want to pick a preset program with one click, so that I can explore the demo without writing COBOL.

#### Acceptance Criteria

1. WHEN `GET /api/presets` succeeds, THE Mainframe_UI SHALL render one tab per Preset using the preset name returned by the Backend_API.
2. WHEN the page loads and `GET /health` first succeeds, THE Mainframe_UI SHALL select `loan_calc` and invoke the Resurrect_Action for `loan_calc` without user action.
3. WHEN the user selects a different Preset tab, THE Mainframe_UI SHALL load that Preset's COBOL source and input schema and invoke the Resurrect_Action for that Preset.
4. IF `GET /api/presets` fails, THEN THE Mainframe_UI SHALL display an error message naming the failed request and a retry control.

### Requirement 6: Resurrect and demo actions

**User Story:** As a visitor, I want to trigger resurrection and the self-heal demo, so that I can see verified output and error recovery.

#### Acceptance Criteria

1. WHEN the user activates the Resurrect_Action, THE Mainframe_UI SHALL send `POST /api/resurrect` with the active `preset_id` and `num_cases` of 150.
2. WHEN the user activates the Demo_Action, THE Mainframe_UI SHALL send `POST /api/resurrect` with the active `preset_id`, `num_cases` of 150 and `demonstrate_self_healing` set to true.
3. WHILE a `POST /api/resurrect` request is in flight, THE Mainframe_UI SHALL disable the Resurrect_Action and the Demo_Action and show a loading indication.
4. WHEN `POST /api/resurrect` returns 200, THE Mainframe_UI SHALL populate the Pipeline_Stepper, Legacy_Panel, Cloud_Dashboard, Parity_Banner and Audit_Trail from the response.
5. IF `POST /api/resurrect` returns a non-2xx status, THEN THE Mainframe_UI SHALL display the error code and message from the response body.
6. IF `POST /api/resurrect` returns status 503, THEN THE Mainframe_UI SHALL display the response message and state that the bundled presets need no LLM key.
7. WHEN a newer `POST /api/resurrect` request supersedes an earlier one, THE Mainframe_UI SHALL discard the response of the earlier request.

### Requirement 7: Three-agent pipeline stepper

**User Story:** As a visitor, I want to see each pipeline stage with its timing and origin, so that I understand how the result was produced.

#### Acceptance Criteria

1. THE Pipeline_Stepper SHALL display the stages Legacy Parser, Cloud Architect and Parity Auditor in that order.
2. WHEN a `POST /api/resurrect` response is received, THE Pipeline_Stepper SHALL show, for each stage, the elapsed time in milliseconds reported by the response.
3. WHEN a `POST /api/resurrect` response is received, THE Pipeline_Stepper SHALL show, for each stage, a source badge reporting the stage source value from the response.
4. WHILE a `POST /api/resurrect` request is in flight, THE Pipeline_Stepper SHALL show each stage in a pending state.
5. IF a stage is absent from the response, THEN THE Pipeline_Stepper SHALL show that stage as not run.

### Requirement 8: Legacy Mainframe panel

**User Story:** As a visitor, I want a 3270-style view of the original COBOL, so that I can see the legacy source and its real output.

#### Acceptance Criteria

1. THE Legacy_Panel SHALL render with background `#0A0E0C` and phosphor-green `#00FF66` monospace text.
2. THE Legacy_Panel SHALL display the active Preset's COBOL source.
3. WHEN the user edits the COBOL source, THE Mainframe_UI SHALL send the edited source as `cobol_code` with the matching `field_schema` on the next Resurrect_Action.
4. WHEN a `POST /api/calculate` response is received, THE Legacy_Panel SHALL display `raw_cobol_output` as live STDOUT.
5. THE Legacy_Panel SHALL provide a control that loads the Sandbox_Test_Snippet into the editor.
6. WHEN the Backend_API returns status 400 with `error` equal to `rejected`, THE Legacy_Panel SHALL display the HTTP status 400, the rejected `token`, the `line` and the `message`.
7. THE Legacy_Panel SHALL display the extracted plain-English business rules and a PIC map listing each field name, PIC clause, direction and scale from the resurrect response.

### Requirement 9: Cloud dashboard inputs and live calculation

**User Story:** As a visitor, I want sliders and inputs that recompute results from the verified engine, so that I can explore scenarios.

#### Acceptance Criteria

1. THE Cloud_Dashboard SHALL render, for each input field in the active Preset's schema, a slider and a decimal text input bound to the same Money_String value.
2. THE Cloud_Dashboard SHALL constrain each slider and input to the `min`, `max` and `step` from the schema.
3. WHEN an input value changes, THE Mainframe_UI SHALL send `POST /api/calculate` with `preset_id` and `inputs` after 250 milliseconds without a further change.
4. WHEN a further input change occurs within the 250 millisecond window, THE Mainframe_UI SHALL cancel the pending calculation and restart the window.
5. WHEN `POST /api/calculate` returns 200, THE Cloud_Dashboard SHALL display each `result` field as a formatted Money_String.
6. IF the user types a value that is not a valid decimal within the field's range and scale, THEN THE Cloud_Dashboard SHALL show an inline validation message and omit the `POST /api/calculate` request.
7. IF `POST /api/calculate` returns 422 with a `field` in the body, THEN THE Cloud_Dashboard SHALL display the message beside the named field.
8. WHEN a newer `POST /api/calculate` request supersedes an earlier one, THE Mainframe_UI SHALL discard the response of the earlier request.

### Requirement 10: Per-preset SVG visuals

**User Story:** As a visitor, I want a chart that suits each program, so that results are easy to grasp.

#### Acceptance Criteria

1. WHERE the active Preset is `loan_calc`, THE Cloud_Dashboard SHALL render a native SVG chart splitting total payable into principal and total interest.
2. WHERE the active Preset is `compound_int`, THE Cloud_Dashboard SHALL render a native SVG chart showing principal against accrued interest at maturity.
3. WHERE the active Preset is `daily_ledger`, THE Cloud_Dashboard SHALL render a native SVG chart showing opening balance, closing balance and fee applied, and an overdraft indicator driven by the overdraft flag.
4. THE Cloud_Dashboard SHALL compute SVG geometry from Money_String values using integer or BigInt arithmetic.
5. WHEN `POST /api/calculate` returns new results, THE Cloud_Dashboard SHALL update the SVG visual to reflect them.

### Requirement 11: Verified Python and export tabs

**User Story:** As a developer, I want to copy or download the verified code, so that I can use it in my own service.

#### Acceptance Criteria

1. THE Cloud_Dashboard SHALL provide a Verified Python tab displaying `verified_python_code` from the resurrect response.
2. WHERE the verified Python code contains no float usage reported by the response, THE Verified Python tab SHALL display a "0 floats verified" badge.
3. WHEN the user activates the copy control, THE Mainframe_UI SHALL write the displayed code to the clipboard and show a confirmation.
4. WHEN the user activates the download control, THE Mainframe_UI SHALL download the displayed code as a `.py` file named after the `preset_id`.
5. WHEN the user opens the Export_Tab, THE Mainframe_UI SHALL send `GET /api/export/{preset_id}` and display `export_fastapi_code`.
6. WHEN the user activates the download control in the Export_Tab, THE Mainframe_UI SHALL download the FastAPI code as a `.py` file named after the `preset_id`.
7. IF `GET /api/export/{preset_id}` returns a non-2xx status, THEN THE Export_Tab SHALL display the error code from the response body.

### Requirement 12: Parity proof banner

**User Story:** As a compliance reviewer, I want a prominent proof of parity, so that I can trust the displayed results.

#### Acceptance Criteria

1. THE Parity_Banner SHALL span the full width of the page below the Legacy_Panel and Cloud_Dashboard.
2. WHEN a `POST /api/resurrect` response is received, THE Parity_Banner SHALL display the exact-match count and total case count in the form "N / M Exact Penny Matches" from the response report.
3. WHEN every case in the report matches, THE Parity_Banner SHALL display "0 Hallucinations" and a passing visual state.
4. IF the report contains any mismatching case, THEN THE Parity_Banner SHALL display a failing visual state and the count of mismatches.
5. WHERE the response reports a cache source for the LLM stages, THE Parity_Banner SHALL state that the live GnuCOBOL verification ran on this request.

### Requirement 13: Self-healing audit trail

**User Story:** As a compliance reviewer, I want to inspect caught defects and repairs, so that I can see why the generated code is trustworthy.

#### Acceptance Criteria

1. WHEN a `POST /api/resurrect` response contains one or more self-healing attempts, THE Audit_Trail SHALL list each attempt in order with its attempt number and outcome.
2. WHEN an attempt contains a counterexample, THE Audit_Trail SHALL render a diff table with the inputs, the COBOL output, the Python output, the differing field and the likely cause.
3. THE Audit_Trail SHALL render counterexample values as Money_Strings without numeric conversion.
4. WHEN the Demo_Action completes with a repaired result, THE Audit_Trail SHALL show the float-drift counterexample followed by the passing final attempt.
5. IF the response contains no self-healing attempts, THEN THE Audit_Trail SHALL display the message "No discrepancies found; no repair needed".

### Requirement 14: Contract parity of types

**User Story:** As a maintainer, I want the frontend types verified against the backend, so that contract drift is detected.

#### Acceptance Criteria

1. WHEN `npx tsc --noEmit` runs in `frontend/`, THE Mainframe_UI SHALL complete with zero type errors.
2. THE Types_Module SHALL declare every field emitted by `to_dict()` of the response classes in `backend/app/resurrection.py` and of the verification report in `backend/app/pbt_verifier.py`.
3. THE Types_Module SHALL declare the error bodies returned by the Backend_API for statuses 400, 404, 413, 422, 500, 502, 503 and 504.

### Requirement 15: Build and quality verification

**User Story:** As a maintainer, I want the frontend to pass build and lint gates, so that it is deployable.

#### Acceptance Criteria

1. WHEN `npm run build` runs in `frontend/`, THE Mainframe_UI SHALL complete with exit code 0.
2. WHEN `npm run lint` runs in `frontend/`, THE Mainframe_UI SHALL complete with exit code 0.
3. WHEN `npx tsc --noEmit` runs in `frontend/`, THE Mainframe_UI SHALL complete with exit code 0.
4. THE Mainframe_UI SHALL include a script or documented command that searches `frontend/src/` for `parseFloat`, `Number(` and `toFixed` applied to money and reports zero matches.

### Requirement 16: Backend smoke check

**User Story:** As a maintainer, I want the frontend verified against the live backend, so that integration works end to end.

#### Acceptance Criteria

1. WHEN the Backend_API runs from the Docker image built from `backend/Dockerfile`, THE smoke check SHALL receive status 200 and `status` equal to `ok` from `GET /health`.
2. WHEN the smoke check posts `{"preset_id": "loan_calc", "num_cases": 150}` to `/api/resurrect`, THE smoke check SHALL receive status 200 and a report with zero mismatches.
3. WHEN the smoke check posts the Sandbox_Test_Snippet as `cobol_code` to `/api/resurrect`, THE smoke check SHALL receive status 400 with `error` equal to `rejected`.
4. WHEN the smoke check requests `GET /api/export/loan_calc`, THE smoke check SHALL receive status 200 with non-empty `export_fastapi_code`.
5. IF Docker is unavailable on the developer machine, THEN THE smoke check SHALL run the same requests against a locally started Uvicorn process and report that Docker was skipped.

### Requirement 17: Commit and publish

**User Story:** As a maintainer, I want the work committed and pushed, so that the hackathon submission is current.

#### Acceptance Criteria

1. WHEN all verification checks pass, THE maintainer workflow SHALL commit `.kiro/specs/mainframe-to-cloud-ui/` and `frontend/` in a single commit.
2. THE maintainer workflow SHALL exclude `node_modules`, `.next` and `.env.local` from the commit.
3. WHEN the commit is created, THE maintainer workflow SHALL run `$env:GIT_TERMINAL_PROMPT="0"; $env:GCM_INTERACTIVE="never"; git push origin main`.
4. IF `git push origin main` fails, THEN THE maintainer workflow SHALL report the git error output and leave the local commit intact.
