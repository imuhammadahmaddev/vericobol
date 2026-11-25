/**
 * Types mirroring the FastAPI / dataclass `to_dict()` wire contracts verbatim.
 * Source of truth: backend/app/{main,catalog,pbt_verifier,resurrection,cobol_runner}.py.
 *
 * All monetary, rate and numeric result values are decimal strings (MoneyString).
 * Never convert them with Number(), parseFloat() or toFixed().
 */

/** Decimal string such as "1234.50". Never a JS number. */
export type MoneyString = string;

export type PresetId = "loan_calc" | "compound_int" | "daily_ledger";

// ---------------------------------------------------------------------------
// GET /health
// ---------------------------------------------------------------------------

export interface HealthResponse {
  status: "ok" | string;
  cobc_available: boolean;
  /** Always a string, possibly "". */
  cobc_version: string;
  /** True when LLM_API_KEY is configured on the backend. */
  llm_configured?: boolean;
}

// ---------------------------------------------------------------------------
// Universal platform contract (inferred by the Legacy_Parser)
// ---------------------------------------------------------------------------

export type IoProtocol =
  | "batch_space"
  | "single_run_lines"
  | "single_run_space"
  | "no_input_static";

export type OutputFormat = "currency" | "percent" | "integer" | "decimal" | "flag" | "text";
export type ChartType = "bar_comparison" | "breakdown" | "waterfall" | "summary";

export interface ContractInputField {
  name: string;
  label: string;
  pic: string;
  scale: number;
  min: MoneyString;
  max: MoneyString;
  step: MoneyString;
}

export interface ContractOutputField {
  name: string;
  label: string;
  pic: string;
  scale: number;
  rounding: "truncate" | "rounded";
  format: OutputFormat;
}

export interface ContractSchema {
  inputs: ContractInputField[];
  outputs: ContractOutputField[];
}

/** Everything /api/calculate-custom needs to re-run one input set. */
export interface CalculateCustomRequestBody {
  cobol_code: string;
  python_code: string;
  schema: ContractSchema;
  io_protocol: IoProtocol;
  /** Decimal strings only. */
  inputs: Record<string, MoneyString>;
}

export interface CalculateCustomResponse {
  result: Record<string, MoneyString>;
  raw_cobol_output: string;
  parity_verified: boolean;
}

export interface ExportCustomRequestBody {
  cobol_code: string;
  python_code: string;
  schema: ContractSchema;
  io_protocol: IoProtocol;
}

export interface ExportCustomResponse {
  filename: string;
  verified_python_code: string;
  export_fastapi_code: string;
  certified: boolean;
}

/** UI progress of one pipeline stage while POST /api/resurrect is in flight. */
export type StageProgress = "pending" | "running" | "done" | "failed";

// ---------------------------------------------------------------------------
// GET /api/presets
// ---------------------------------------------------------------------------

export interface PresetInputField {
  name: string;
  label: string;
  min: MoneyString;
  max: MoneyString;
  step: MoneyString;
  /** Present only for some fields (e.g. overdraft_threshold). */
  default?: MoneyString;
}

/** One starter template from GET /api/presets: raw COBOL text only. */
export interface StarterTemplate {
  id: string;
  title: string;
  cobol_code: string;
}

export interface PresetSummary {
  preset_id: PresetId;
  name: string;
  description: string;
  cobol_source: string;
  input_schema: PresetInputField[];
  sample_input: Record<string, MoneyString>;
}

// ---------------------------------------------------------------------------
// POST /api/calculate
// ---------------------------------------------------------------------------

export interface CalculateRequestBody {
  preset_id: string;
  inputs: Record<string, MoneyString>;
}

export interface CalculateResponse {
  preset_id: string;
  result: Record<string, MoneyString>;
  raw_cobol_output: string;
}

// ---------------------------------------------------------------------------
// Parity reports (POST /api/verify, and embedded in /api/resurrect)
// ---------------------------------------------------------------------------

export type LikelyCause =
  | "float_drift"
  | "rounded_vs_truncation"
  | "scale_mismatch"
  | "overflow"
  | "python_error"
  | "unknown";

export interface Counterexample {
  inputs: Record<string, string>;
  /** Values may be the literal "n/a" when output could not be parsed. */
  cobol_output: Record<string, string>;
  /** Values may be the literal "n/a" when the Python side errored. */
  python_output: Record<string, string>;
  differing_field: string;
  /** Decimal string, or the literal "n/a". */
  penny_delta: string;
  likely_cause: LikelyCause;
}

export interface ParityReport {
  total_cases: number;
  passed_cases: number;
  failed_cases: number;
  /** Decimal string, e.g. "100.00". */
  parity_score_pct: string;
  verified: boolean;
  execution_ms: number;
  /** Up to 20 lines. */
  raw_cobol_sample_output: string[];
  /** Up to 5 entries. */
  counterexamples: Counterexample[];
}

/** POST /api/verify field schema entry (backend FieldSpec). */
export interface VerifyFieldSpec {
  name: string;
  min: MoneyString;
  max: MoneyString;
  step: MoneyString;
}

/**
 * POST /api/verify request. Exactly one of preset_id / cobol_code.
 * num_cases is 1..250 here (backend MAX_CASES). No UI component calls this endpoint.
 */
export interface VerifyRequestBody {
  preset_id?: string;
  cobol_code?: string;
  python_code?: string;
  field_schema?: VerifyFieldSpec[];
  num_cases?: number;
  simulate_float_bug?: boolean;
}

export type VerifyResponse = ParityReport;

// ---------------------------------------------------------------------------
// POST /api/resurrect
// ---------------------------------------------------------------------------

export type StageName = "legacy_parser" | "cloud_architect" | "parity_auditor";
export type StageStatus = "ok" | "failed" | "skipped";
export type StageSource = "llm" | "cache" | "live" | "deterministic";

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

/** UI-derived (not a response field). Computed by `deriveAttemptOutcome`. */
export type AttemptOutcome = "passed" | "repaired" | "failed";

export interface FinalReport extends ParityReport {
  certified: boolean;
  status: "passed" | "failed";
}

export type Rounding = "ROUND_DOWN" | "ROUND_HALF_UP";

export interface PicMapping {
  name: string;
  pic: string;
  scale: string;
  rounding: Rounding;
}

export interface DashboardInputMeta {
  name: string;
  label: string;
  type: "integer" | "decimal";
  min: MoneyString;
  max: MoneyString;
  step: MoneyString;
  /** Realistic starting value chosen by the legacy parser (optional). */
  default?: MoneyString;
}

export interface DashboardOutputMeta {
  name: string;
  label: string;
  format: OutputFormat;
}

export interface DashboardMetadata {
  inputs: DashboardInputMeta[];
  outputs: DashboardOutputMeta[];
}

/** Backend FieldContractSpec, used only for custom COBOL. */
export interface FieldContractSpecBody {
  name: string;
  pic: string;
  direction: "input" | "output";
  /** 0..18 */
  scale?: number | null;
  min?: MoneyString | null;
  max?: MoneyString | null;
  step?: MoneyString | null;
  label?: string | null;
  rounding?: Rounding | null;
}

/**
 * POST /api/resurrect request. Exactly one of preset_id / cobol_code.
 * num_cases must be 150..200 for this endpoint (RESURRECT_MIN/MAX_CASES);
 * the UI always sends 150.
 */
export interface ResurrectRequestBody {
  preset_id?: string;
  cobol_code?: string;
  field_schema?: FieldContractSpecBody[];
  num_cases: number;
  demonstrate_self_healing?: boolean;
  force_live_llm?: boolean;
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
  /** Universal platform fields (absent on older responses). */
  program_title?: string;
  description?: string;
  io_protocol?: IoProtocol;
  contract?: ContractSchema;
  certified?: boolean;
}

// ---------------------------------------------------------------------------
// GET /api/export/{preset_id}
// ---------------------------------------------------------------------------

export interface ParityCertificate {
  certified: boolean;
  parity_score_pct: string;
  method: string;
  note: string;
}

export interface ExportResponse {
  preset_id: string;
  verified_python_code: string;
  export_fastapi_code: string;
  certified: boolean;
  parity_certificate: ParityCertificate;
}

// ---------------------------------------------------------------------------
// Error bodies. FastAPI always wraps them as { detail: ... }.
// Statuses: 400 (rejected), 404 (unknown_preset / bundle_missing / unknown preset_id),
// 413 (string), 422 (field / invalid_request / compile_failed / Pydantic array),
// 500 (reference_model_failed, run_failed, row_count_mismatch, parity_mismatch),
// 502 (llm_call_failed), 503 (llm_key_missing / budget_exhausted), 504 (run_timeout).
// ---------------------------------------------------------------------------

/** 400 from /api/resurrect sandbox rejection. */
export interface RejectedErrorDetail {
  error: "rejected";
  token: string;
  line: number;
  message: string;
}

/**
 * Generic object detail.
 * NOTE: the wire key is `error`, NOT `kind`. The backend dataclass
 * VerificationError.kind is renamed to `error` by `_error()`; do not "fix" this.
 * 422 from /api/calculate carries `field` and `message` (no `error`), so `error` is optional.
 */
export interface SimpleErrorDetail {
  error?: string;
  message: string;
  /** Present on 422 from /api/calculate, and on some 500s. Read from detail.field, not body.field. */
  field?: string;
  [k: string]: unknown;
}

/** 404 from /api/resurrect and /api/export. */
export interface UnknownPresetErrorDetail {
  error: "unknown_preset" | "bundle_missing";
  valid_ids: string[];
}

/** 502 from /api/resurrect. */
export interface LlmCallFailedErrorDetail {
  error: "llm_call_failed";
  stage: StageName;
  message: string;
}

/** 503 from /api/resurrect. */
export interface LlmUnavailableErrorDetail {
  error: "llm_key_missing" | "budget_exhausted";
  message: string;
}

/** 422 from Pydantic validation (e.g. malformed field_schema). */
export interface PydanticFieldError {
  loc: (string | number)[];
  msg: string;
  type: string;
}

/** 404 / 413 / malformed-body 422 may be a bare string. */
export type ErrorDetail =
  | string
  | RejectedErrorDetail
  | UnknownPresetErrorDetail
  | LlmCallFailedErrorDetail
  | LlmUnavailableErrorDetail
  | SimpleErrorDetail
  | PydanticFieldError[];

export interface ErrorBody {
  detail: ErrorDetail;
}

export class ApiError extends Error {
  public readonly status: number;
  public readonly detail: ErrorDetail;

  constructor(status: number, detail: ErrorDetail) {
    super(`API error ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}
