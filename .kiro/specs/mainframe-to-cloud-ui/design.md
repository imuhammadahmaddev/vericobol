# Design Document

## Overview

The Mainframe_UI is a Next.js (App Router, TypeScript, Tailwind CSS) single-page experience in `frontend/`. It has one typed API_Client module, a `types.ts` module mirroring the FastAPI/dataclass `to_dict()` contracts verbatim, and a set of presentational components for the Legacy_Panel (3270 style), Cloud_Dashboard (inputs, SVG charts, code tabs), Parity_Banner, and Audit_Trail. No charting or state-management library is added; React state (`useState`/`useReducer`/`useEffect`) and native SVG cover every requirement.

All money, rate and counterexample values are carried end-to-end as strings (`Money_String`). The only arithmetic the frontend performs on them is integer/BigInt arithmetic for slider step indices and SVG geometry — never `Number()`, `parseFloat`, or `toFixed`.

## Contract Verification Against the Backend

I read `backend/app/catalog.py`, `backend/app/pbt_verifier.py`, `backend/app/resurrection.py`, `backend/app/main.py`, `backend/app/cobol_runner.py`, and `backend/app/pipeline_cache.py` and traced every `to_dict()` / Pydantic response model to build `types.ts`. Below is the field-by-field contract and the discrepancies found against the requirements document's assumptions.

### `GET /health` (main.py `health()`)
```json
{"status": "ok", "cobc_available": true, "cobc_version": "cobc (GnuCOBOL) 3.x..."}
```
Matches Requirement 4 exactly. `cobc_version` is always a string (possibly `""`), never absent — Requirement 4.6's "WHERE `cobc_version` is non-empty" guard is still correct.

### `GET /api/presets` (catalog.py `load_presets()`)
Array of:
```json
{"preset_id": "...", "name": "...", "description": "...", "cobol_source": "...", "input_schema": [{"name","label","min","max","step","default"?}], "sample_input": {...}}
```
**Discrepancy 1 (field name):** the requirements/glossary refer informally to "COBOL source" and "input schema" without naming the JSON keys. The actual keys are `cobol_source` (not `source` or `cobol_code`) and `input_schema` (array of `{name, label, min, max, step}`, with `default` present only for `overdraft_threshold`). `types.ts` uses these exact names. No requirement text contradicts this, but Requirement 8.2/9.1 ("COBOL source", "input field schema") must be read against these exact field names — recorded here for traceability.

### `POST /api/calculate` success body (main.py `_calculate_blocking`)
```json
{"preset_id": "...", "result": {"<output_name>": "123.45", ...}, "raw_cobol_output": "123.45 67.89 ...\n"}
```
Requirement 8.4 says "display `raw_cobol_output` as live STDOUT" and Requirement 9.5 says "display each `result` field" — both match exactly.

### `POST /api/calculate` error bodies
- 422 missing/invalid field: `{"detail": {"field": "<name>", "message": "<name> <reason>"}}` (also a bare-string `detail` for malformed JSON/body-shape errors).
- 404 unknown preset: `{"detail": "unknown preset_id: '...'"}` (string, not object).
- 413 body too large: `{"detail": "Request body exceeds N bytes"}` (string).
- 500 `reference_model_failed` / `run_failed` / `row_count_mismatch` / `parity_mismatch`: `{"detail": {"error": "<kind>", "message": "...", "field"?: "<name>"}}`.
- 504 `run_timeout`: `{"detail": {"error": "run_timeout", "message": "..."}}`.

**Discrepancy 2:** Requirement 9.7 says "IF `POST /api/calculate` returns 422 with a `field` in the body...". The `field` key lives one level inside `detail.field`, not at the top level of the response body (FastAPI always wraps error payloads in `{"detail": ...}`, see `_error()` in `main.py`). `types.ts` models every error response as `{ detail: string | Record<string, unknown> }` and the API_Client narrows `detail` before reading `field`/`token`/`line`/`error`/`message`. This is noted so the component code does not read `body.field` directly.

### `POST /api/verify` success body = `ParityReport.to_dict()` (pbt_verifier.py)
```json
{
  "total_cases": 150, "passed_cases": 150, "failed_cases": 0,
  "parity_score_pct": "100.00", "verified": true, "execution_ms": 842.3,
  "raw_cobol_sample_output": ["...", ...up to 20],
  "counterexamples": [Counterexample.to_dict(), ...up to 5]
}
```
`Counterexample.to_dict()`:
```json
{"inputs": {"<name>":"..."}, "cobol_output": {"<name>":"..."}, "python_output": {"<name>":"..."} ,
 "differing_field": "...", "penny_delta": "0.01" , "likely_cause": "float_drift|rounded_vs_truncation|scale_mismatch|overflow|python_error|unknown"}
```
Requirement 12.2 ("N / M Exact Penny Matches") maps to `passed_cases` / `total_cases`. Requirement 13.2's "likely cause" maps to `likely_cause` with the exact 6-value union above; `types.ts` declares it as a string literal union, not a bare `string`, so an unexpected value is a type error that surfaces in code review rather than silently rendering blank.

**Discrepancy 3:** `penny_delta` and some `cobol_output`/`python_output` values can be the literal string `"n/a"` (when the Python side errored or output couldn't be parsed) rather than a decimal string — `types.ts` types `penny_delta` and the output maps as `string`, not a stricter decimal-string branded type, specifically because of this `"n/a"` case; a stricter type would be incorrect.

`VerificationError.to_dict()` (raised as 400/422/500/504 depending on `kind`): `{"kind": "...", "message": "..."}` — but `main.py`'s `_error()` wraps it again, so the client actually receives `{"detail": {"error": "<kind>", "message": "..."}}` for `/api/verify` (the `kind` key from the dataclass is renamed to `error` by `_verify_blocking`/`_error`). **Discrepancy 4:** the dataclass method name (`kind`) and the wire key (`error`) differ; `types.ts` only models the wire shape (`error`, `message`), not the dataclass field name, and this is called out explicitly in a code comment in `types.ts` to prevent a future contributor from "fixing" it to `kind`.

### `POST /api/resurrect` success body = `ResurrectResponse.to_dict()` (resurrection.py)
```json
{
  "preset_id": "loan_calc" | null,
  "cache_hit": true, "llm_used": false,
  "stages": [{"name":"legacy_parser|cloud_architect|parity_auditor","status":"ok|failed|skipped","elapsed_ms":12,"source":"llm|cache|deterministic"}],
  "business_rules": ["..."],
  "pic_mappings": [{"name":"...","pic":"...","scale":"2","rounding":"ROUND_DOWN|ROUND_HALF_UP"}],
  "dashboard_metadata": {"inputs":[{"name","label","type":"integer|decimal","min","max","step"}], "outputs":[{"name","label","format":"currency|integer|decimal|flag|percent"}]},
  "verified_python_code": "...",
  "export_fastapi_code": "...",
  "audit_trail": [{"attempt":1,"parity_score_pct":"100.00","passed_cases":150,"total_cases":150,"counterexample": Counterexample|null}],
  "final_report": {...ParityReport.to_dict() fields..., "certified": true, "status": "passed|failed"}
}
```
Requirement 7 (Pipeline_Stepper) matches `stages[].{name,status,elapsed_ms,source}` exactly — `name` is one of exactly 3 literal strings, `source` one of exactly 3 literal strings; `types.ts` uses literal unions for both so the stepper's switch statements are exhaustively checked.

**Discrepancy 5 (important, affects Requirement 11.2):** there is **no field anywhere in `ResurrectResponse.to_dict()` reporting float usage or a float count**. Requirement 11.2 says: 'WHERE the verified Python code contains no float usage reported by the response, THE Verified Python tab SHALL display a "0 floats verified" badge.' No such signal exists on the wire. The closest available truth is `final_report.verified` (bool) combined with `final_report.status === "passed"`, which the Decimal-parity proof already establishes implies no float-drift defect survived verification. **Resolution:** the badge is shown whenever `final_report.certified === true`, labeled "0 Hallucinations · Verified Decimal-only" instead of literally counting floats, since the backend never counts them. This is implemented as a derived boolean in the Cloud_Dashboard, not a new field read from the response. Flagged here as a contract gap between requirements.md and the actual backend, resolved by reusing `certified`.

**Discrepancy 6:** Requirement 12.5 says: "WHERE the response reports a cache source for the LLM stages, THE Parity_Banner SHALL state that the live GnuCOBOL verification ran on this request." There is no single "cache source" boolean; the per-stage `source` field (`"cache"`) on `stages[0]`/`stages[1]` is the signal, together with the top-level `cache_hit` boolean, which is the simpler and already-present field for this exact purpose. **Resolution:** the banner reads `cache_hit` (not the per-stage `source`) to decide whether to show the "cached pipeline stages — live GnuCOBOL verification still ran" note, since `cache_hit` is purpose-built for it and `audit_trail`/`final_report` always come from a live run regardless of `cache_hit`.

**Discrepancy 7:** Requirement 13.1 says "attempt number and outcome" — `AttemptRecord.to_dict()` has no `outcome` field; outcome must be derived: an attempt's outcome is `"passed"` when it is the last entry and `final_report.status === "passed"`, else `"repaired"` (not last, counterexample present, and a later attempt exists) or `"failed"` (last entry, status failed). `types.ts` leaves `AttemptRecord` exactly as the wire shape (`attempt, parity_score_pct, passed_cases, total_cases, counterexample`); outcome is a UI-derived enum computed in a pure helper function (`deriveAttemptOutcome`), not a response field.

### `POST /api/resurrect` error bodies
- 404 unknown preset: `{"detail": {"error": "unknown_preset", "valid_ids": ["loan_calc","compound_int","daily_ledger"]}}`.
- 400 rejected (sandbox): `{"detail": {"error": "rejected", "token": "...", "line": 12, "message": "..."}}` — matches Requirement 8.6 exactly (status/token/line/message), confirmed field-for-field against `ValidationError` in `cobol_runner.py`.
- 422 invalid_request / compile_failed / PipelineStageError body: `{"detail": {"error": "<kind>", "message": "..."}}` (sometimes with extra keys from `exc.body`, e.g. `field_schema` validation errors return a **list** of `{"loc": [...], "msg": "...", "type": "..."}` instead of an object — this is Pydantic's native error array, passed straight through).
- 503 `llm_key_missing` / `budget_exhausted`: `{"detail": {"error": "llm_key_missing"|"budget_exhausted", "message": "..."}}`.
- 502 `llm_call_failed`: `{"detail": {"error": "llm_call_failed", "stage": "legacy_parser|cloud_architect|parity_auditor", "message": "..."}}`.
- 413: `{"detail": "Request body exceeds N bytes"}` (string, from the raw ASGI middleware, not JSON-decoded through FastAPI's normal path — confirmed in `BodySizeLimitMiddleware._reject`).

**Discrepancy 8:** Requirement 6.5 says "display the error code and message from the response body" generically; 6.6 singles out 503. The 422 case from Pydantic validation (malformed `field_schema`) is an **array**, not an object with `error`/`message` keys. `types.ts` models the 422 body as `ResurrectValidationErrorDetail[] | ResurrectErrorDetail | string` (a union) and the API_Client's error type carries the raw parsed `detail` so the UI can branch on `Array.isArray(detail)`.

### `GET /api/export/{preset_id}` (main.py `export_preset`)
```json
{"preset_id":"...", "verified_python_code":"...", "export_fastapi_code":"...", "certified": true,
 "parity_certificate": {"certified": true, "parity_score_pct": "100.00", "method": "...", "note": "..."}}
```
404 body: `{"detail": {"error": "unknown_preset", "valid_ids": [...]}} ` or `{"detail": {"error": "bundle_missing", "valid_ids": [...]}}`.

Matches Requirement 11.1/11.5 field names exactly (`verified_python_code`, `export_fastapi_code`).

### Request bodies
- `POST /api/calculate`: `{"preset_id": string, "inputs": {<field>: string|number}}` — the backend accepts `inputs` directly or a flat body (`data.get("inputs", data)`), but the client always sends the nested `{preset_id, inputs}` shape for clarity; both are valid per `main.py`, only the nested form is documented in `types.ts`.
- `POST /api/resurrect`: `{"preset_id"?: string, "cobol_code"?: string, "field_schema"?: FieldContractSpec[], "num_cases": number (150-200), "demonstrate_self_healing"?: boolean, "force_live_llm"?: boolean}` — exactly one of `preset_id`/`cobol_code` required; `num_cases` bounds for `/api/resurrect` are **150–200** (`RESURRECT_MIN_CASES`/`RESURRECT_MAX_CASES`), not 1–250 as for `/api/verify` (`MAX_CASES`). **Discrepancy 9:** Requirement 6.1/6.2 say "send ... with `num_cases` of 150" — 150 is valid under both endpoints' bounds, so no contract violation, but `types.ts` encodes the resurrect-specific bound as a comment since TypeScript cannot express a numeric range at the type level; the component hardcodes `150` and never exposes a free-text `num_cases` input, so the bound is enforced by construction.
- `POST /api/verify`: not driven by any UI control per the requirements (no requirement references `/api/verify` as a UI action); `types.ts` still declares its request/response types for Requirement 2.2/14.2 completeness, but no component calls it. This is intentional, not a gap — recorded so a future contributor does not assume a missing feature.

All field names above are copied verbatim from the backend source files cited; `types.ts` is generated to match them exactly, field for field.

## Architecture

```
frontend/
├── src/
│   ├── app/
│   │   ├── layout.tsx            # Tailwind globals, metadata
│   │   └── page.tsx              # top-level state: activePreset, health, resurrectResult, calcResult
│   ├── lib/
│   │   ├── api.ts                # API_Client: the only module calling fetch()
│   │   ├── types.ts              # Types_Module: mirrors backend to_dict()/Pydantic contracts
│   │   ├── money.ts              # Money_String helpers: stepIndexToMoneyString, clampToSchema, validateDecimal, compareDecimalStrings (all string/BigInt based)
│   │   └── sandboxSnippet.ts     # Sandbox_Test_Snippet constant (CALL "SYSTEM" example)
│   ├── hooks/
│   │   ├── useHealth.ts          # Requirement 4: poll /health, cancel on unmount
│   │   ├── useResurrect.ts       # Requirement 6/7: request sequencing (latest-wins), loading state
│   │   └── useCalculate.ts       # Requirement 9: debounced /api/calculate with cancel-and-restart + latest-wins
│   └── components/
│       ├── HealthPill.tsx                 # Req 4
│       ├── ColdStartBanner.tsx            # Req 4
│       ├── PresetTabs.tsx                 # Req 5
│       ├── PipelineStepper.tsx            # Req 7
│       ├── LegacyPanel/
│       │   ├── LegacyPanel.tsx            # Req 8.1-8.2
│       │   ├── CobolSourceEditor.tsx      # Req 8.3, 8.5
│       │   ├── RawStdoutView.tsx          # Req 8.4
│       │   ├── RejectionDetail.tsx        # Req 8.6
│       │   └── BusinessRulesAndPicMap.tsx # Req 8.7
│       ├── CloudDashboard/
│       │   ├── CloudDashboard.tsx         # Req 9 container
│       │   ├── FieldSliderInput.tsx       # Req 9.1-9.2, 9.6-9.7
│       │   ├── charts/
│       │   │   ├── LoanChart.tsx          # Req 10.1
│       │   │   ├── CompoundChart.tsx      # Req 10.2
│       │   │   └── LedgerChart.tsx        # Req 10.3-10.4
│       │   ├── VerifiedPythonTab.tsx      # Req 11.1-11.4
│       │   └── ExportTab.tsx              # Req 11.5-11.7
│       ├── ParityBanner.tsx               # Req 12
│       └── AuditTrail.tsx                 # Req 13
└── .env.local.example
```

### Data flow

`page.tsx` holds `activePresetId`, the latest `ResurrectResponse | null`, the latest `CalculateResponse | null`, and health state (via `useHealth`). Preset selection and the Resurrect/Demo actions call `useResurrect`'s `run()`, which tags each call with a monotonically increasing request id; a response is applied to state only if its id is still the highest issued (Requirement 6.7/9.8). `useCalculate` debounces 250 ms per keystroke/slider-drag and applies the same latest-wins guard.

## Components and Interfaces

### `lib/types.ts` (excerpt — full file mirrors every section above)

```typescript
export type MoneyString = string; // decimal string, never a JS number

export interface HealthResponse {
  status: "ok" | string;
  cobc_available: boolean;
  cobc_version: string;
}

export interface PresetInputField {
  name: string;
  label: string;
  min: MoneyString;
  max: MoneyString;
  step: MoneyString;
  default?: MoneyString;
}

export interface PresetSummary {
  preset_id: "loan_calc" | "compound_int" | "daily_ledger";
  name: string;
  description: string;
  cobol_source: string;
  input_schema: PresetInputField[];
  sample_input: Record<string, MoneyString>;
}

export interface CalculateResponse {
  preset_id: string;
  result: Record<string, MoneyString>;
  raw_cobol_output: string;
}

export type LikelyCause =
  | "float_drift" | "rounded_vs_truncation" | "scale_mismatch"
  | "overflow" | "python_error" | "unknown";

export interface Counterexample {
  inputs: Record<string, string>;
  cobol_output: Record<string, string>;
  python_output: Record<string, string>;
  differing_field: string;
  penny_delta: string; // decimal string or the literal "n/a"
  likely_cause: LikelyCause;
}

export interface ParityReport {
  total_cases: number;
  passed_cases: number;
  failed_cases: number;
  parity_score_pct: string;
  verified: boolean;
  execution_ms: number;
  raw_cobol_sample_output: string[];
  counterexamples: Counterexample[];
}

export type StageName = "legacy_parser" | "cloud_architect" | "parity_auditor";
export type StageStatus = "ok" | "failed" | "skipped";
export type StageSource = "llm" | "cache" | "deterministic";

export interface StageRecord {
  name: StageName;
  status: StageStatus;
  elapsed_ms: number;
  source: StageSource;
}

export interface AttemptRecord {
  attempt: number;
  parity_score_pct: string;
  passed_cases: number;
  total_cases: number;
  counterexample: Counterexample | null;
}

export interface FinalReport extends ParityReport {
  certified: boolean;
  status: "passed" | "failed";
}

export interface PicMapping {
  name: string;
  pic: string;
  scale: string;
  rounding: "ROUND_DOWN" | "ROUND_HALF_UP";
}

export interface DashboardInputMeta {
  name: string; label: string; type: "integer" | "decimal";
  min: MoneyString; max: MoneyString; step: MoneyString;
}
export interface DashboardOutputMeta {
  name: string; label: string;
  format: "currency" | "integer" | "decimal" | "flag" | "percent";
}
export interface DashboardMetadata {
  inputs: DashboardInputMeta[];
  outputs: DashboardOutputMeta[];
}

export interface ResurrectResponse {
  preset_id: string | null;
  cache_hit: boolean;
  llm_used: boolean;
  stages: StageRecord[];
  business_rules: string[];
  pic_mappings: PicMapping[];
  dashboard_metadata: DashboardMetadata;
  verified_python_code: string;
  export_fastapi_code: string;
  audit_trail: AttemptRecord[];
  final_report: FinalReport;
}

// Error bodies — see "Contract Verification" discrepancies 2, 4, 8
export interface RejectedErrorDetail { error: "rejected"; token: string; line: number; message: string; }
export interface SimpleErrorDetail { error: string; message: string; [k: string]: unknown; }
export interface PydanticFieldError { loc: (string | number)[]; msg: string; type: string; }
export type ErrorDetail = string | SimpleErrorDetail | RejectedErrorDetail | PydanticFieldError[];

export class ApiError extends Error {
  constructor(public status: number, public detail: ErrorDetail) { super(`API error ${status}`); }
}
```

### `lib/api.ts`

```typescript
const BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  const text = await res.text();
  const body = text ? JSON.parse(text) : null; // no numeric coercion: JSON.parse keeps quoted decimals as strings
  if (!res.ok) throw new ApiError(res.status, body?.detail ?? body);
  return body as T;
}

export const api = {
  health: () => request<HealthResponse>("/health"),
  presets: () => request<PresetSummary[]>("/api/presets"),
  calculate: (body: { preset_id: string; inputs: Record<string, string> }, signal?: AbortSignal) =>
    request<CalculateResponse>("/api/calculate", { method: "POST", body: JSON.stringify(body), signal }),
  resurrect: (body: ResurrectRequestBody, signal?: AbortSignal) =>
    request<ResurrectResponse>("/api/resurrect", { method: "POST", body: JSON.stringify(body), signal }),
  exportPreset: (presetId: string) => request<ExportResponse>(`/api/export/${presetId}`),
};
```

`JSON.stringify` on a `MoneyString` (already a `string`) always emits a quoted JSON string token — this is the mechanism behind Requirement 3.2, not a special-cased serializer.

### `lib/money.ts`

Pure, dependency-free functions, all operating on strings/BigInt:
- `stepIndexToMoneyString(index: bigint, min: MoneyString, step: MoneyString, scale: number): MoneyString` — `min_units + index * step_units`, formatted back with the decimal point reinserted at `scale`.
- `moneyStringToStepIndex(value, min, step, scale): bigint` — inverse, for initializing slider position.
- `clampToSchema(value, field): MoneyString` — clamps into `[min, max]` and rounds to the nearest `step` multiple, all in integer-units space.
- `validateDecimal(raw: string, field): { ok: true; value: MoneyString } | { ok: false; message: string }` — parses with a regex (`^-?\d+(\.\d+)?$`), checks scale `<= field` scale implied by `step`'s decimal places, and range.
- `compareDecimalStrings(a, b): -1 | 0 | 1` — sign/integer/fraction comparison without `Number()`.

### Hooks

- `useHealth()`: polls `GET /health` every 3000 ms while not `ok`; stops polling once `ok`; cleans up its `setTimeout`/`AbortController` on unmount (Req 4.7).
- `useResurrect()`: exposes `run(body)`, `loading`, `data`, `error`; internally keeps `latestRequestId` and an `AbortController` per call; ignores (and aborts) stale responses.
- `useCalculate()`: exposes `trigger(inputs)`; debounces via `setTimeout(250)`, clears the previous timeout on a new call (Req 9.3-9.4), and applies the same latest-wins guard as `useResurrect` (Req 9.8).

## Data Models

All data models are the TypeScript interfaces in `lib/types.ts` shown above under "Components and Interfaces," generated field-for-field from the backend's `to_dict()`/Pydantic shapes documented in "Contract Verification Against the Backend." There is no separate frontend-only persisted data model: the Mainframe_UI holds only transient React state mirroring the latest `HealthResponse`, `PresetSummary[]`, `ResurrectResponse`, and `CalculateResponse`, keyed by nothing more durable than the component tree (no local storage, no database, per `tech.md`/`structure.md`). The two frontend-local shapes not present on the wire are:

```typescript
// Request body sent to /api/resurrect — assembled by the UI, not a backend dataclass.
export interface ResurrectRequestBody {
  preset_id?: string;
  cobol_code?: string;
  field_schema?: FieldContractSpecBody[]; // mirrors backend FieldContractSpec, used only for custom COBOL
  num_cases: number; // always 150, see Discrepancy 9
  demonstrate_self_healing?: boolean;
  force_live_llm?: boolean;
}

// UI-derived (not a response field, see Discrepancy 7)
export type AttemptOutcome = "passed" | "repaired" | "failed";
```

## Error Handling

- Network/parse failures and non-2xx responses surface as `ApiError` (status + typed `detail`), caught at the call site (`useResurrect`, `useCalculate`, preset loader) and rendered by the component that owns that part of the UI (Legacy_Panel for rejection errors, Cloud_Dashboard for calculate field errors, a top-level banner for preset-list failures per Req 5.4).
- The Legacy_Panel specifically narrows `detail` to `RejectedErrorDetail` only when `status === 400 && typeof detail === "object" && detail.error === "rejected"`; any other shape falls back to a generic message using `detail.message` if present, else `String(detail)`.
- The Cloud_Dashboard narrows to `SimpleErrorDetail` with a `field` key for 422s; a `PydanticFieldError[]` (array) is rendered as a bulleted list of `loc.join(".")`: `msg`.

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Decimal-string passthrough on parse

For any JSON response body where a field's wire value is a numeric-looking string (e.g. `"123.456000"`, `"-0.01"`, `"100000000.00"`), parsing that body through the API_Client's `request()` function yields that field as the identical string, never a JavaScript `number`, regardless of digit count or decimal places.

**Validates: Requirements 2.6**

### Property 2: Outgoing money values are always quoted JSON strings

For any `MoneyString` value held in UI state, serializing it into a `/api/calculate` or `/api/resurrect` request body produces a JSON token that is a quoted string, never a bare numeric literal, for any value including those with many digits or trailing zeros.

**Validates: Requirements 3.2**

### Property 3: Step-index/Money-String round trip is exact

For any schema field with `min`, `step` (both decimal strings of consistent scale) and any integer step index `i` within `[0, (max-min)/step]`, converting `i` to a `MoneyString` and back to a step index with `moneyStringToStepIndex` returns `i` exactly, with no accumulated drift even when `step` has a scale other than 2 (e.g. the rate fields' `0.001` step).

**Validates: Requirements 3.4**

### Property 4: Health_Pill state is a pure function of the health response

For any `HealthResponse`-shaped object (`status` any string, `cobc_available` any boolean, `cobc_version` any string), the Health_Pill's displayed state and text are fully determined by those three fields: online text only when `status === "ok"`; the "GnuCOBOL unavailable" text whenever `cobc_available === false`, independent of `status`; the `cobc_version` text appears if and only if `cobc_version` is non-empty.

**Validates: Requirements 4.4, 4.5, 4.6**

### Property 5: Latest-wins request sequencing

For any sequence of two calls to the same triggering hook (`useResurrect.run` or `useCalculate.trigger`) where the second call is issued before the first call's response arrives, the state applied after both responses have arrived always reflects the response of the call issued second, regardless of which response resolves first.

**Validates: Requirements 6.7, 9.8**

### Property 6: Pipeline_Stepper is a total, order-preserving function of `stages`

For any array of zero or more `StageRecord`-shaped objects drawn from the three known stage names, the Pipeline_Stepper renders exactly one row per of the three fixed stage names (`legacy_parser`, `cloud_architect`, `parity_auditor`) in that fixed order; a stage present in the input is rendered with its `elapsed_ms` and `source` reproduced verbatim, and a stage absent from the input is rendered as "not run".

**Validates: Requirements 7.1, 7.2, 7.3, 7.5**

### Property 7: Rejection detail rendering is complete and verbatim

For any `RejectedErrorDetail`-shaped body (arbitrary `token` string, arbitrary `line` integer, arbitrary `message` string) delivered with HTTP status 400, the Legacy_Panel's rendered rejection text contains the status code 400 and the exact `token`, `line`, and `message` values, unmodified.

**Validates: Requirements 8.6**

### Property 8: PIC map rendering is a lossless one-row-per-entry mapping

For any array of `PicMapping`-shaped objects (arbitrary `name`, `pic`, `scale`, `rounding` strings), the rendered PIC map table contains exactly one row per array entry, and each row reproduces that entry's `name`, `pic`, `scale`, and `rounding` values verbatim with no reformatting of the PIC clause or scale string.

**Validates: Requirements 8.7**

### Property 9: Schema clamping always yields an in-range, step-aligned value

For any schema field (`min`, `max`, `step`, all decimal strings of consistent scale, `min <= max`) and any candidate decimal-string value (in or out of range, on or off the step grid), `clampToSchema` returns a `MoneyString` that is within `[min, max]` inclusive and is an exact integer multiple of `step` above `min`, computed entirely with integer/BigInt arithmetic.

**Validates: Requirements 9.2, 10.4**

### Property 10: Change-event debounce coalesces to exactly one trigger

For any sequence of input-change events fired on `useCalculate`, where every gap between consecutive events is strictly less than 250 ms, exactly one `/api/calculate` call is issued, carrying the inputs from the last event in the sequence, fired 250 ms after that last event; any event arriving less than 250 ms after the previous one cancels the previous pending call.

**Validates: Requirements 9.3, 9.4**

### Property 11: Validation acceptance is equivalent to parse-and-range success

For any input string and any schema field (`min`, `max`, decimal places implied by `step`), `validateDecimal` returns `ok: true` if and only if the string parses as a finite decimal with at most the field's implied decimal places and its numeric value lies within `[min, max]`; whenever it returns `ok: false`, no `/api/calculate` request is issued for that change.

**Validates: Requirements 9.6**

### Property 12: 422 field-error rendering targets the named field

For any `SimpleErrorDetail`-shaped body containing a `field` key whose value matches the name of a field in the active preset's schema, the Cloud_Dashboard renders that body's `message` beside that field's input and no other field's input.

**Validates: Requirements 9.7**

### Property 13: Per-preset chart geometry is proportional, reconstructive, and float-free

For any `loan_calc` result object (`principal`, `total_interest`, `total_payable` as decimal strings) the rendered chart's principal-segment and interest-segment widths are each proportional to their value and their sum exactly equals the segment representing `total_payable`, with every intermediate computation performed in integer/BigInt arithmetic (never `Number()`/float), so that computing the geometry twice from the same input strings yields byte-identical output. The equivalent proportional-and-reconstructive invariant holds for `compound_int` (`principal` vs `accrued_interest` summing to `maturity_amount`) and `daily_ledger` (`opening_balance`, `closing_balance`, `fee_applied`, with the overdraft indicator driven solely by `overdraft_flag`).

**Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.5**

### Property 14: Parity_Banner text and visual state are a pure function of the report counts

For any `ParityReport`-shaped object with `0 <= passed_cases <= total_cases` and `failed_cases === total_cases - passed_cases`, the Parity_Banner always displays the text `"{passed_cases} / {total_cases} Exact Penny Matches"`; it shows "0 Hallucinations" and a passing visual state if and only if `failed_cases === 0`, and otherwise shows a failing visual state together with the exact `failed_cases` count.

**Validates: Requirements 12.2, 12.3, 12.4**

### Property 15: Audit_Trail rendering is lossless and verbatim, including the empty case

For any array of `AttemptRecord`-shaped objects (including the empty array), the Audit_Trail renders exactly one entry per attempt, in order, with its `attempt` number; for any attempt whose `counterexample` is non-null, the diff table reproduces every value of `inputs`, `cobol_output`, `python_output`, `differing_field`, and `likely_cause` verbatim as strings with no numeric parsing; when the array is empty, the Audit_Trail renders exactly the text "No discrepancies found; no repair needed" and no diff table.

**Validates: Requirements 13.1, 13.2, 13.3, 13.5**

## Testing Strategy

**Unit tests** (Vitest + React Testing Library):
- `HealthPill`/`ColdStartBanner` with 2-3 concrete `HealthResponse` examples (ok, degraded, unreachable).
- Preset tab selection triggers the expected `resurrect` call shape (1 example per preset, Req 5.2/5.3).
- `CobolSourceEditor` loads the `Sandbox_Test_Snippet` and wires `cobol_code`/`field_schema` into the next request (Req 8.3/8.5).
- Clipboard/download controls call the mocked browser APIs with the exact displayed string and `${preset_id}.py`/`${preset_id}_api.py` filenames (Req 11.3, 11.4, 11.6).
- Backend smoke check (Req 16) as a small Node/Python script run against the Docker image or a local Uvicorn fallback — not part of the Vitest suite.

**Property tests** (`fast-check`, minimum 100 runs each), one test module per property above, generating:
- Arbitrary decimal strings (including `"0.00"`, negative, many-digit, trailing-zero cases) for Properties 1, 2, 3, 9, 11, 13.
- Arbitrary `HealthResponse`, `StageRecord[]`, `PicMapping[]`, `ParityReport`, `AttemptRecord[]`, `Counterexample` objects built from the exact `types.ts` shapes for Properties 4, 6, 7, 8, 14, 15.
- Arbitrary interleavings of two async resolution orders (via a controllable fake promise scheduler) for Property 5, and arbitrary event-timing sequences (via a fake clock) for Property 10.
- Arbitrary `field`-matching/non-matching error bodies for Property 12.

Each property test is tagged `Feature: mainframe-to-cloud-ui, Property N: <title>` and cites the design property it implements in a comment above the test.
