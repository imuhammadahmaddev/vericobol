# Design Document

## Overview

The Resurrection_Pipeline is a new orchestration layer in `backend/app/` that sits in front of the existing Verification_Engine (`pbt_verifier.run_verification`, `cobol_runner.validate_source`, `cobol_runner.CobolRunner`). It adds two endpoints, `POST /api/resurrect` and `GET /api/export/{preset_id}`, a three-stage pipeline (Legacy_Parser, Cloud_Architect, Parity_Auditor), a self-healing retry loop, a demonstration mode built on the existing `FLOAT_BUG_REGISTRY`, an in-memory LRU result cache, a static preset bundle file, a budget-capped OpenAI-compatible LLM client, a stdlib-only MCP server, two Kiro hooks, and a devcontainer.

The pipeline never invents the input/output contract: for Presets it is read from `backend/cobol_samples/presets.json` (already loaded by `catalog.py` / `pbt_verifier._fields`); for Custom_COBOL it is taken verbatim from the caller's `field_schema`. The LLM is used only to produce prose (business rules), metadata (PIC mappings, dashboard metadata) and a Decimal Python function body; that function body is never trusted directly — it is always run through the existing `pbt_verifier.load_restricted_function` / `run_user_python` sandbox inside `run_verification`.

Reuse map (no duplication of existing logic):

| Existing component | Reused for |
|---|---|
| `cobol_runner.validate_source` | Rejecting dangerous Custom_COBOL before any LLM call |
| `cobol_runner.CobolRunner` | Single compiled binary per verification run |
| `pbt_verifier.run_verification` | The one and only source of a `ParityReport` / `Counterexample` |
| `pbt_verifier.generate_cases` | Case generation (unchanged) |
| `pbt_verifier.load_restricted_function` / `run_user_python` | Sandboxed execution of any Python the pipeline produces (cached, bundled, LLM-generated, or float-bug demo) |
| `reference_models.REGISTRY` / `FLOAT_BUG_REGISTRY` | Demo_Mode attempt 1 (buggy) and attempt 2 (exact) sources |
| `catalog.py` (`DecimalJSONResponse`, `load_presets`) | JSON rendering with Decimal-as-string, preset metadata |
| `main.py` (`_VERIFY_LOCK`, `BodySizeLimitMiddleware`, `_money`/`_JsonFloat`, CORS) | Concurrency serialization and float-safe JSON parsing, extended to the new endpoints |

## Architecture

```
POST /api/resurrect
      │
      ▼
┌─────────────────────────┐
│ Request validation       │  ResurrectRequest (pydantic) — mirrors VerifyRequest's
│ (main.py)                 │  _money/_JsonFloat/_shape pattern; field_schema required
└───────────┬───────────────┘  for cobol_code; num_cases in [150,200]
            │
            ▼
┌─────────────────────────┐
│ resurrection.py           │  Pipeline.run(request) -> ResurrectResponse
│   Pipeline                │
│   ├─ cache_key()          │  sha256(normalize(source) + demo_flag)
│   ├─ try cache/bundle      │  PipelineCache (LRU, 64) + preset_bundles.json
│   ├─ Legacy_Parser         │  llm_client.extract_rules(...)  -> business_rules, pic_mappings
│   ├─ Cloud_Architect       │  llm_client.generate_python(...) -> verified_python_code,
│   │                        │                                      dashboard_metadata
│   ├─ Parity_Auditor        │  SelfHealingLoop.run(cobol_source, code_fn, request)
│   │     │                  │
│   │     ▼                  │
│   │  run_verification()  ◄─┼── Verification_Engine (unchanged)
│   │     │                  │
│   │     ▼                  │
│   │  on failure: Cloud_Architect.repair(counterexample) → retry (≤2)
│   └─ export_fastapi_code   │  export_template.render(verified_python_code, schema)
└───────────┬───────────────┘
            ▼
     ResurrectResponse (DecimalJSONResponse)
```

```
GET /api/export/{preset_id}
      │
      ▼
preset_bundles.json[preset_id][demonstrate_self_healing=false]
      │  (no LLM, no compiler)
      ▼
{preset_id, verified_python_code, export_fastapi_code, certified}
```

## Components and Interfaces

### 1. `backend/app/llm_client.py` — `LLMClient`

```python
@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str
    model: str

class LLMKeyMissing(Exception):
    """Raised when LLM_API_KEY is unset, empty, or a known .env.example placeholder."""

class LLMCallError(Exception):
    """Raised on timeout or an unparseable response. Carries `stage: str`."""
    def __init__(self, stage: str, message: str): ...

class BudgetExceeded(Exception):
    """Raised when cumulative cost would reach $2.00."""

class LLMClient:
    def __init__(self, config: LLMConfig | None, budget_tracker: "BudgetTracker"): ...

    @classmethod
    def from_env(cls, budget_tracker: "BudgetTracker") -> "LLMClient":
        """Reads LLM_BASE_URL, LLM_API_KEY, LLM_MODEL only. Raises nothing; a
        missing/placeholder key means `self.config is None`."""

    def require_key(self) -> None:
        """Raises LLMKeyMissing if self.config is None. Called before any live call."""

    def extract_rules(self, cobol_source: str) -> ParsedRules: ...
    def generate_python(self, cobol_source: str, rules: ParsedRules,
                         schema: "ContractSchema") -> ArchitectOutput: ...
    def repair_python(self, cobol_source: str, rules: ParsedRules,
                       schema: "ContractSchema",
                       counterexample: Counterexample,
                       previous_code: str) -> ArchitectOutput: ...
```

- `from_env` treats `LLM_API_KEY` as missing when it is unset, empty after `.strip()`, or equal (case-insensitive) to any placeholder string literal found in `.env.example` (e.g. `your_api_key_here`). This directly implements `LLM_Key_Missing` without hardcoding a second copy of the placeholder — `from_env` reads `.env.example` once at import time via `Path(__file__).resolve().parents[2] / ".env.example"` and extracts the value after `LLM_API_KEY=`.
- Every network call goes through one private `_call(stage: str, prompt: str) -> str` that: checks `require_key()`, checks `budget_tracker.can_spend(estimated_cost)`, sets `timeout=30`, caps the response body read at 64 KiB (reads `response.text[:65536]` or uses the SDK's byte-limited stream), and on any `TimeoutError`/`JSONDecodeError`/SDK exception raises `LLMCallError(stage, "...")` whose message never includes `api_key` or the raw provider payload. On success it calls `budget_tracker.record(actual_cost)`.
- `ParsedRules`, `ArchitectOutput`, `ContractSchema` are small frozen dataclasses defined in `resurrection.py` (see below) — the LLM client only knows how to turn a prompt into one of them via strict JSON-schema parsing (`json.loads(text, parse_float=Decimal)`); a parse failure is itself an `LLMCallError`.
- `BudgetTracker` (`backend/app/llm_client.py`) is a tiny process-wide object: `cumulative_cost_usd: Decimal`, `can_spend(estimate) -> bool` (`cumulative + estimate < Decimal("2.00")`), `record(actual) -> None`. It is a module-level singleton (`_BUDGET = BudgetTracker()`), mirroring the existing `_shared_runner()` pattern.

### 2. `backend/app/resurrection.py` — orchestration

```python
@dataclass(frozen=True)
class FieldContract:
    name: str; pic: str; scale: int; direction: Literal["input", "output"]
    min: Decimal | None = None; max: Decimal | None = None; step: Decimal | None = None

@dataclass(frozen=True)
class ContractSchema:
    input_fields: tuple[FieldContract, ...]
    output_fields: tuple[FieldContract, ...]

@dataclass(frozen=True)
class ParsedRules:
    business_rules: list[str]
    pic_mappings: list[dict[str, str]]   # {name, pic, scale, rounding}

@dataclass(frozen=True)
class ArchitectOutput:
    verified_python_code: str
    dashboard_metadata: dict[str, Any]

@dataclass(frozen=True)
class StageRecord:
    name: Literal["legacy_parser", "cloud_architect", "parity_auditor"]
    status: Literal["ok", "failed", "skipped"]
    elapsed_ms: float
    source: Literal["llm", "cache", "deterministic"]

@dataclass(frozen=True)
class AttemptRecord:
    attempt: int                      # 1-based
    parity_score_pct: str
    passed_cases: int
    total_cases: int
    counterexample: Counterexample | None   # present iff this attempt failed

@dataclass(frozen=True)
class FinalReport:
    report: ParityReport
    certified: bool
    status: Literal["passed", "failed"]

@dataclass(frozen=True)
class ResurrectResponse:
    preset_id: str | None
    cache_hit: bool
    llm_used: bool
    stages: list[StageRecord]
    business_rules: list[str]
    pic_mappings: list[dict[str, str]]
    dashboard_metadata: dict[str, Any]
    verified_python_code: str
    export_fastapi_code: str
    audit_trail: list[AttemptRecord]
    final_report: FinalReport

class PipelineStageError(Exception):
    """Carries an HTTP status, a stage name and a body; main.py maps it directly."""
    def __init__(self, status: int, stage: str | None, body: Any): ...

class Pipeline:
    def __init__(self, llm: LLMClient, cache: "PipelineCache",
                 bundles: dict[str, dict[str, Any]], runner: CobolRunner | None = None): ...

    def run(self, *, preset_id: str | None, cobol_source: str | None,
            schema: ContractSchema, num_cases: int,
            demonstrate_self_healing: bool, force_live_llm: bool) -> ResurrectResponse: ...
```

`Pipeline.run` logic, in order:

1. **Resolve contract**: `schema` is built by `main.py` from either `_fields(preset_id)` (Preset) or the caller's `field_schema` (Custom_COBOL) before `Pipeline.run` is called — the pipeline itself never derives a contract from COBOL text or LLM output (Req 2.3, 2.4).
2. **Cache/bundle lookup** (Preset only, `force_live_llm=False`): `key = cache_key(cobol_source, demonstrate_self_healing)`. Check `bundles` first (static, Requirement 5 priority is unspecified between cache and bundle since a bundle entry is pre-seeded into the cache at startup — see Data Models), then `cache.get(key)`. A hit sets `cache_hit=True`, `llm_used=False`, marks `legacy_parser`/`cloud_architect` stage records with `source="cache"` and skips both LLM stages entirely (zero `LLMClient` calls, so zero `LLM_API_KEY` reads — Req 5.4, 6.5).
3. **Legacy_Parser** (on a miss, or `force_live_llm=True`): `llm.extract_rules(cobol_source)` → `ParsedRules`. Stage record `source="llm"`.
4. **Cloud_Architect**: `llm.generate_python(cobol_source, rules, schema)` → `ArchitectOutput`. For Demo_Mode (Preset only) this stage is replaced by a **deterministic** step: attempt 1's code is `inspect.getsource(FLOAT_BUG_REGISTRY[preset_id])` wrapped to satisfy the `calculate(**kwargs)` entry-point contract required by `load_restricted_function`; stage `source="deterministic"`, zero LLM calls for this attempt.
5. **Parity_Auditor** / `SelfHealingLoop.run(...)` (see below) produces `audit_trail` and `FinalReport`.
6. **Export rendering**: `export_template.render(verified_python_code, schema)` → `export_fastapi_code` (pure string templating, no LLM).
7. **Cache write**: if this was a live run (`force_live_llm=True` or a miss) and `final_report.certified`, `cache.put(key, snapshot)` (Req 5.8).
8. Any `VerificationError` raised by `run_verification` anywhere in the loop is wrapped into `PipelineStageError(status, "parity_auditor", body)` and propagated without a retry (Req 3.8); `main.py` catches it and returns the HTTP error — the loop's `try/except` around each `run_verification()` call only catches `VerificationError`, never a parity failure (a parity failure is a normal `ParityReport` with `verified=False`, not an exception).

### 3. `SelfHealingLoop` (in `resurrection.py`)

```python
class SelfHealingLoop:
    MAX_RETRIES = 2

    def __init__(self, llm: LLMClient, runner: CobolRunner): ...

    def run(self, cobol_source: str, preset_id: str | None,
            rules: ParsedRules, schema: ContractSchema,
            first_attempt_code: str, first_attempt_source: Literal["llm", "deterministic"],
            num_cases: int) -> tuple[list[AttemptRecord], FinalReport, str]:
        """Returns (audit_trail, final_report, final_code)."""
```

Loop body (attempts numbered 1..3):

```
code = first_attempt_code
for attempt in 1..3:
    report = run_verification(preset_id, cobol_source, python_code=code,
                               num_cases=num_cases, runner=self._runner)   # may raise VerificationError
    record = AttemptRecord(attempt, report.parity_score_pct, report.passed_cases,
                            report.total_cases,
                            counterexample=report.counterexamples[0] if report.counterexamples else None)
    audit_trail.append(record)
    if report.verified:                      # parity_score_pct == "100.00"
        return audit_trail, FinalReport(report, certified=True, status="passed"), code
    if attempt == 3:
        return audit_trail, FinalReport(report, certified=False, status="failed"), code
    code = llm.repair_python(cobol_source, rules, schema,
                              report.counterexamples[0], previous_code=code)
```

This directly satisfies Req 3.1–3.7: the first counterexample of the failing report feeds the next repair call (3.1); the loop body runs at most 3 times, i.e. ≤2 retries (3.2); `run_verification` is called again with the identical `cobol_source` (3.3, since `cobol_source` is loop-invariant); a `report.verified` (parity 100.00) returns immediately with `certified=True` (3.4); every iteration appends exactly one `AttemptRecord` with a `counterexample` that is `None` only on the passing branch (3.5); after the loop exits via `attempt == 3` the response carries `status="failed"`, `certified=False` (3.6) and `code` is whatever was verified last, not attempt 1's (3.7).

### 4. `backend/app/export_template.py`

```python
def render(verified_python_code: str, schema: ContractSchema) -> str:
    """Returns a self-contained FastAPI module source string.

    Embeds `verified_python_code` verbatim inside a module that also defines
    a minimal FastAPI app with one POST /calculate route built from
    `schema.input_fields`/`output_fields`. Pure string formatting — no LLM,
    no exec. The caller is responsible for ensuring verified_python_code is
    float-free (checked once, upstream, by the Req 1.9 scanner tests).
    """
```

### 5. `backend/app/pipeline_cache.py` — `PipelineCache`

```python
class PipelineCache:
    """Thread-safe in-memory LRU, max 64 entries, keyed on sha256 hex digest."""
    def __init__(self, max_entries: int = 64): ...
    def get(self, key: str) -> CachedResult | None: ...
    def put(self, key: str, value: CachedResult) -> None: ...

def normalize_cobol(source: str) -> str:
    """\\r\\n and \\r -> \\n; strip trailing whitespace per line; strip leading
    and trailing blank lines. Idempotent: normalize(normalize(s)) == normalize(s)."""

def cache_key(cobol_source: str, demonstrate_self_healing: bool) -> str:
    payload = normalize_cobol(cobol_source).encode("utf-8") + (b"\\x01" if demonstrate_self_healing else b"\\x00")
    return hashlib.sha256(payload).hexdigest()
```

Backed by `collections.OrderedDict`: `get` calls `move_to_end` (recording recency), `put` evicts `next(iter(od))` (the least-recently-used key) via `popitem(last=False)` whenever `len(od) > max_entries` after insertion. A `threading.Lock` guards both methods, matching the `_VERIFY_LOCK` convention in `main.py`.

### 6. `backend/cobol_samples/preset_bundles.json`

Structure: `{preset_id: {"false": BundleEntry, "true": BundleEntry}}` for each of the three Presets, where `"false"`/`"true"` are the string forms of `demonstrate_self_healing` (JSON object keys must be strings). Each `BundleEntry` holds `business_rules`, `pic_mappings`, `dashboard_metadata`, `verified_python_code`, `export_fastapi_code`, and `stages` (the two deterministic `StageRecord`s for `legacy_parser`/`cloud_architect`, `source="cache"`). At process startup (module import of `resurrection.py`, mirroring `catalog.load_presets()`), every bundle entry is loaded once and also seeded into the live `PipelineCache` under its `cache_key`, so "hits the Pipeline_Cache or the Preset_Bundle_File" (Req 5.4-5.6) is implemented as a single lookup path: the bundle file is the cache's initial contents, not a second code path.

### 7. `backend/app/main.py` additions

- `ResurrectRequest` pydantic model, modeled on `VerifyRequest`: reuses `MoneyValue`/`_money`/`_JsonFloat` for schema bounds, adds `field_schema: list[FieldContractSpec] | None`, `demonstrate_self_healing: bool = False`, `force_live_llm: bool = False`, `num_cases: int = Field(150, ge=150, le=200)`. A `model_validator` enforces Req 2.1 (cobol_code requires field_schema, each field needs name/pic/direction), 2.5 (exactly one of preset_id/cobol_code), 7.3 (not both).
- `_resurrect_body_limit = 256 * 1024` reuses `BodySizeLimitMiddleware`'s pattern but is enforced again in the handler against the actual body (chunked uploads), same as `/api/verify`.
- `POST /api/resurrect` handler: validates, runs `validate_source` first when `cobol_code` is present (returning its 4xx **before** touching the LLM — Req 2.9), constructs `ContractSchema`, calls `pipeline.run(...)` inside `run_in_threadpool` under the same `_VERIFY_LOCK` used by `/api/verify` (Req 7.7 — one COBOL compile-and-run at a time across *both* endpoints), and renders `ResurrectResponse.to_dict()` through `DecimalJSONResponse`. Catches `PipelineStageError` → `_error(status, body)`; catches `LLMKeyMissing`/`BudgetExceeded` → 503; catches `LLMCallError` → 502 with `{"stage": exc.stage, "message": ...}`.
- `GET /api/export/{preset_id}` handler: validates `preset_id in PRESET_IDS` (else 404 with the id list), reads the bundle's `demonstrate_self_healing=false` entry, returns `{preset_id, verified_python_code, export_fastapi_code, certified: true}` directly from the parsed `preset_bundles.json` — no `Pipeline`, no `LLMClient`, no `CobolRunner` import path is touched (Req 8.4).

### 8. `backend/mcp_server/server.py`

A single-file, stdlib-only stdio JSON-RPC 2.0 loop:

```python
def main() -> None:
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            write(error_response(None, -32700, "Parse error")); continue
        handle(msg)   # dispatches by msg["method"]; unknown -> -32601
```

- Imports `backend.app.cobol_runner` and `backend.app.pbt_verifier` directly (same process, no HTTP), so `compile_and_run_cobol` reuses `validate_source` + `CobolRunner` exactly as `/api/verify` does, and `verify_cobol_parity` calls `run_verification` exactly as `/api/resurrect` does — the three surfaces (REST, MCP, pipeline) share one implementation of each primitive.
- `tools/list` returns fixed JSON Schemas for `list_cobol_presets` (no params), `compile_and_run_cobol` (`{cobol_source: string, input_rows: string[]}`), `verify_cobol_parity` (`{preset_id: string, num_cases?: integer}`).
- All writes go to `sys.stdout` with `\n`-terminated single-line JSON; all logging/diagnostics go to `sys.stderr`.

### 9. Config, hooks, devcontainer

- `.kiro/settings/mcp.json`: one server entry `vericobol-gnucobol` with `"command": "docker", "args": ["run", "-i", "--rm", "vericobol-backend", "python", "-m", "mcp_server.server"]` — no secrets, no env block.
- `.kiro/hooks/guard-no-float.kiro.hook`: `PostFileSave` (or editor-save equivalent) trigger, matcher restricted to `backend/app/**/*.py` and `backend/cobol_samples/**/*.py`, action runs a small stdlib scanner (reusing the AST approach of `tests/test_no_float.py`) and reports matches; never calls a shell with unescaped file content.
- `.kiro/hooks/verify-cobol-parity.kiro.hook`: trigger on saving a `.cbl` file under `backend/cobol_samples/`, action runs `pytest backend/tests/test_loan_properties.py backend/tests/test_compound_properties.py backend/tests/test_ledger_properties.py` (argument list, no shell string interpolation of the file path).
- `.devcontainer/devcontainer.json`: `"build": {"dockerfile": "../backend/Dockerfile", "context": "../backend"}`, `"remoteEnv": {"COBC_PATH": "cobc"}`, `"forwardPorts": [8000]`, no `secrets`/`containerEnv` entries carrying key values.

## Data Models

```
ContractSchema              = (input_fields: FieldContract[], output_fields: FieldContract[])
FieldContract                = (name, pic, scale, direction, min?, max?, step?)
ParsedRules                  = (business_rules: str[], pic_mappings: {name,pic,scale,rounding}[])
ArchitectOutput              = (verified_python_code: str, dashboard_metadata: object)
StageRecord                  = (name, status, elapsed_ms, source)
Counterexample  (existing)   = (inputs, cobol_output, python_output, differing_field, penny_delta, likely_cause)
ParityReport    (existing)   = (total_cases, passed_cases, failed_cases, parity_score_pct, verified, execution_ms, raw_cobol_sample_output, counterexamples)
AttemptRecord                 = (attempt: int, parity_score_pct: str, passed_cases: int, total_cases: int, counterexample: Counterexample | null)
FinalReport                   = (report: ParityReport, certified: bool, status: "passed"|"failed")
ResurrectResponse             = (preset_id, cache_hit, llm_used, stages: StageRecord[],
                                  business_rules, pic_mappings, dashboard_metadata,
                                  verified_python_code, export_fastapi_code,
                                  audit_trail: AttemptRecord[], final_report: FinalReport)
CachedResult (pipeline_cache) = (business_rules, pic_mappings, dashboard_metadata,
                                  verified_python_code, export_fastapi_code, stages: StageRecord[])
```

All `Decimal`-bearing fields (`FieldContract.min/max/step`, every money value nested in `ParityReport`/`Counterexample`) are serialized exclusively through `DecimalJSONResponse` (existing `catalog.py`), so they reach the wire as JSON strings, never JSON numbers (Req 7.4) — this is the same rendering path `/api/verify` already uses, extended with no new serialization logic.

## Error Handling

| Condition | Status | Body shape | Source |
|---|---|---|---|
| `cobol_code` without `field_schema` | 422 | pydantic error, `loc` includes `field_schema` | `ResurrectRequest` validator |
| `field_schema` entry missing name/pic/direction, or empty list | 422 | pydantic error naming the field/index | `ResurrectRequest` validator |
| neither / both of `preset_id`/`cobol_code` | 422 | pydantic error | `ResurrectRequest` validator |
| unknown `preset_id` | 404 | `{"error": "unknown_preset", "valid_ids": [...]}` | handler |
| `num_cases` outside [150,200] | 422 | pydantic error | `Field(ge=150, le=200)` |
| float literal in `field_schema` bounds | 422 | pydantic error (`_money` raises) | `MoneyValue` validator (reused) |
| Custom_COBOL rejected by sandbox | 400 | `{"error": "rejected", "token", "line", "message"}` | `validate_source`, before any LLM call |
| `demonstrate_self_healing=true` + Custom_COBOL | 422 | `{"error": "invalid_request", "message": "demonstration mode requires a preset_id"}` | handler, before pipeline |
| body > 256 KiB | 413 | plain detail string | reused `BodySizeLimitMiddleware` pattern |
| `LLM_Key_Missing` + Custom_COBOL, or `force_live_llm` + missing key | 503 | `{"error": "llm_key_missing", "message": "..."}` naming `LLM_API_KEY` | `LLMKeyMissing` → handler |
| budget reached $2.00 | 503 | `{"error": "budget_exhausted", "message": "..."}` | `BudgetExceeded` → handler |
| LLM timeout / unparseable response | 502 | `{"error": "llm_call_failed", "stage": "...", "message": "..."}` (no key, no raw payload) | `LLMCallError` → handler |
| `run_verification` raises `VerificationError` anywhere in the loop | mapped per existing `_verify_blocking` table (400/422/500/504) | `{"error": kind, "message": ...}` | re-used from `main.py._verify_blocking` |
| healing fails after 2 retries | 200 | full `audit_trail`, `final_report.status="failed"`, `certified=false` | `SelfHealingLoop` (not an error) |
| `GET /api/export/{preset_id}` unknown id | 404 | `{"error": "unknown_preset", "valid_ids": [...]}` | handler |

All exceptions from LLM parsing (malformed JSON from the model) are caught at the `LLMClient._call` boundary and normalized to `LLMCallError`, so no raw provider exception or payload ever reaches `main.py` or the response body.

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Stage record shape is always well-formed

For any accepted resurrect request (any combination of preset/custom, cache hit/miss, demo on/off), every element of `stages` has a `name` in `{legacy_parser, cloud_architect, parity_auditor}`, a `status` in `{ok, failed, skipped}`, a non-negative `elapsed_ms`, and a `source` in `{llm, cache, deterministic}`.

**Validates: Requirements 1.2**

### Property 2: `num_cases` is passed through unchanged

For any accepted request with `num_cases` in [150, 200], the `total_cases` of `final_report.report` equals that `num_cases` value, and every `AttemptRecord.total_cases` in `audit_trail` equals it too.

**Validates: Requirements 1.4, 7.9**

### Property 3: Generated code and export are float-free and load under the restricted sandbox

For any `ResurrectResponse` produced by the pipeline (cached, bundled, or LLM-generated), `verified_python_code` and `export_fastapi_code` contain no `float` literal, no `float(...)` call and no `math.pow`/`**` with a float operand, and `verified_python_code` parses and loads successfully through `pbt_verifier.load_restricted_function`.

**Validates: Requirements 1.9**

### Property 4: Dashboard metadata and PIC mappings reflect only the resolved contract

For any request (Preset or Custom_COBOL with a `field_schema`) and for any adversarial LLM stub output that invents extra fields or alters names/bounds, the `name`, `min`, `max` and `step` of every input control in `dashboard_metadata` and the `name` of every entry in `pic_mappings` come from the resolved `ContractSchema` (the Preset catalog or the caller's `field_schema`), never from the LLM.

**Validates: Requirements 1.7, 2.3, 2.4**

### Property 5: Export module embeds and matches the verified function

For any `verified_python_code` that defines a `calculate` entry point, rendering it through `export_template.render` produces a syntactically valid Python module containing that exact source, and calling the exported module's calculation route on a given input set produces the same output as calling `verified_python_code`'s `calculate` directly through the restricted loader.

**Validates: Requirements 1.8**

### Property 6: Missing or invalid `field_schema` is always rejected before the sandbox and LLM run

For any request carrying `cobol_code` with `field_schema` omitted, empty, or containing an entry missing `name`, `pic` or `direction`, the pipeline returns an HTTP 4xx response identifying the offending requirement, and makes zero `CobolRunner` compile calls and zero `LLMClient` calls.

**Validates: Requirements 2.1, 2.2**

### Property 7: Unknown preset identifiers are always rejected with the valid list

For any string that is not one of `loan_calc`, `compound_int`, `daily_ledger` supplied as `preset_id`, the pipeline returns HTTP 4xx (404) whose body lists exactly those three identifiers.

**Validates: Requirements 2.6**

### Property 8: `num_cases` outside [150, 200] is always rejected

For any integer `n`, a request with `num_cases = n` is accepted if and only if `150 <= n <= 200`; every rejected value yields HTTP 4xx without reaching the Verification_Engine.

**Validates: Requirements 2.7**

### Property 9: Float-literal monetary bounds are always rejected

For any `field_schema` whose `min`, `max` or `step` is written as a JSON float literal (a fractional or exponent form rather than a string or integer), the request is rejected with HTTP 4xx, mirroring the existing `/api/verify` `Money_Value` rule.

**Validates: Requirements 2.8, 7.5**

### Property 10: Sandbox rejection always precedes and prevents any LLM call

For any `cobol_code` containing a construct the Security_Validator rejects (`CALL`, file I/O, `SYSTEM`, environment access, etc.), the pipeline returns the validator's HTTP 4xx error and the `LLMClient` records zero calls for that request.

**Validates: Requirements 2.9**

### Property 11: Repair always uses the prior attempt's first counterexample

For any failing verification attempt that is not the final one, the `Counterexample` passed to the next `repair_python` call is exactly `attempt_report.counterexamples[0]` of the attempt that just failed.

**Validates: Requirements 3.1**

### Property 12: The self-healing loop never exceeds 3 attempts

For any sequence of attempt outcomes that never reaches parity 100.00, the loop runs attempt 1 plus at most 2 retries (3 attempts total) and then stops, regardless of how many further repairs would be offered.

**Validates: Requirements 3.2**

### Property 13: Every retry re-verifies against the same COBOL source

For any multi-attempt run, every call to `run_verification` across all attempts of one request receives the identical `cobol_source` value.

**Validates: Requirements 3.3**

### Property 14: Reaching 100.00 parity always stops the loop and certifies

For any attempt sequence in which attempt `k` (k in {1,2,3}) first reaches a parity score of `100.00`, the loop performs exactly `k` attempts, `final_report.certified` is `true`, `final_report.status` is `"passed"`, and no attempt `k+1` is made.

**Validates: Requirements 3.4, 4.6**

### Property 15: Audit trail records are sequential and carry a counterexample exactly on failure

For any run, `audit_trail` is ordered by `attempt` 1..N with no gaps, and for every record `counterexample is not None` if and only if that attempt's parity score was below `100.00`.

**Validates: Requirements 3.5**

### Property 16: Exhausting retries always yields an uncertified, failed, HTTP-200 result carrying the last code

For any attempt sequence in which no attempt reaches `100.00` within 3 attempts, the response has HTTP status 200, exactly 3 `audit_trail` records, `final_report.status == "failed"`, `final_report.certified == False`, and `verified_python_code` equal to the code used in attempt 3 (not an earlier attempt's code).

**Validates: Requirements 3.6, 3.7**

### Property 17: A `VerificationError` always stops the loop without a retry

For any attempt at which `run_verification` raises a `VerificationError` instead of returning a `ParityReport`, the pipeline returns the mapped HTTP error immediately, and `run_verification` is not called again for that request.

**Validates: Requirements 3.8**

### Property 18: Demonstration mode always ends certified with a recorded repair

For any of the three Presets run with `demonstrate_self_healing=true`, the final response has `final_report.report.parity_score_pct == "100.00"` and `len(audit_trail) >= 2`.

**Validates: Requirements 4.4**

### Property 19: Demonstration mode is rejected for Custom_COBOL

For any request carrying `cobol_code` and `demonstrate_self_healing=true`, the pipeline returns HTTP 4xx stating that demonstration mode requires a Preset, and never calls `run_verification`.

**Validates: Requirements 4.5**

### Property 20: Cache key normalization is idempotent and separates demo variants

For any COBOL source text, `normalize_cobol(normalize_cobol(s)) == normalize_cobol(s)`; for any two source texts that normalize to the same string, their `cache_key` is equal when `demonstrate_self_healing` is equal and different when `demonstrate_self_healing` differs.

**Validates: Requirements 5.1, 5.2**

### Property 21: Cache and bundle hits always skip the LLM and report the hit flags

For any Preset request with `force_live_llm=false` that resolves to a cache or bundle entry, the response has `cache_hit=true`, `llm_used=false`, and the `LLMClient` records zero calls.

**Validates: Requirements 5.4**

### Property 22: Cache and bundle hits still run live verification and report its fresh result

For any cache/bundle-hit request, `run_verification` is still called at least once, and `final_report` equals the `ParityReport` produced by that fresh call rather than any value stored in the cache or bundle — including when the fresh call fails, in which case `certified` is `false` even though the entry was previously certified.

**Validates: Requirements 5.5, 5.6**

### Property 23: `force_live_llm` always bypasses cache and bundle

For any Preset request with `force_live_llm=true`, the pipeline calls the `LLMClient` (`llm_used=true`) even when a cache or bundle entry already exists for that source and demo flag.

**Validates: Requirements 5.7**

### Property 24: A certifying live run is always retrievable from the cache afterward

For any live run (`force_live_llm=true` or an initial miss) that ends `certified=true`, an immediately following identical request (same normalized source, same demo flag, `force_live_llm=false`) is a cache hit with zero further LLM calls.

**Validates: Requirements 5.8**

### Property 25: The cache never exceeds its capacity and always evicts least-recently-used first

For any sequence of cache insertions and lookups, the cache never holds more than 64 entries, and whenever an insertion would exceed the limit, the entry evicted is the one that was least recently inserted-or-retrieved among all current entries.

**Validates: Requirements 5.9**

### Property 26: `LLM_Key_Missing` always yields HTTP 503 when a live call is required

For any request that requires a live LLM call (Custom_COBOL, or `force_live_llm=true` on a Preset) while `LLM_API_KEY` is unset, empty, or a known placeholder, the pipeline returns HTTP 503 naming `LLM_API_KEY`, and no LLM network call is attempted.

**Validates: Requirements 6.4, 6.6**

### Property 27: A bundle/cache hit never reads `LLM_API_KEY`

For any Preset request that resolves to a cache or bundle entry without `force_live_llm`, the process never reads the `LLM_API_KEY` environment variable while handling that request.

**Validates: Requirements 6.5**

### Property 28: LLM failures never leak secrets or raw provider output

For any LLM call that times out or returns an unparseable response, the resulting HTTP 502 body contains the failing stage name and excludes the configured `LLM_API_KEY` value and the raw provider response text.

**Validates: Requirements 6.9**

### Property 29: Untrusted LLM-generated code is always mediated by the restricted loader

For any `python_code` returned by the (stubbed) LLM — including code containing a forbidden construct such as `__import__` or `eval` — the pipeline never executes it outside `load_restricted_function`/`run_user_python`; a forbidden construct surfaces as a failed verification attempt (and is retried or ends in healing failure), never as an unguarded exception or a 500 that bypasses the sandbox.

**Validates: Requirements 6.8**

### Property 30: The cumulative LLM budget is enforced at exactly $2.00

For any sequence of live LLM calls with tracked per-call costs, every call is permitted while the cumulative recorded cost remains below `$2.00`, and the first call that would bring the cumulative cost to `$2.00` or beyond is rejected with HTTP 503 before any network request is attempted.

**Validates: Requirements 6.10**

### Property 31: Every monetary value in the response is a JSON string

For any `ResurrectResponse`, decoding the raw response bytes as JSON yields a `str` (never a JSON number) for every field known to carry a monetary or scaled value: `field_schema`-derived bounds echoed in `dashboard_metadata`, and every money field inside `final_report` and `audit_trail[*].counterexample`.

**Validates: Requirements 7.4**

### Property 32: Concurrent verification runs never overlap

For any set of concurrent requests to `/api/resurrect` and `/api/verify` together, no two COBOL compile-and-run executions are in progress at the same time; their time intervals are pairwise disjoint.

**Validates: Requirements 7.7**

### Property 33: The MCP `compile_and_run_cobol` tool matches direct `CobolRunner` use

For any valid COBOL source accepted by the Security_Validator and any list of input rows, the MCP server's `compile_and_run_cobol` tool result (`stdout`, `stderr`, `exit_code`) equals the result of calling `CobolRunner.compile_and_run_custom` directly with the same arguments.

**Validates: Requirements 9.6, 9.7**

## Testing Strategy

**Unit / example tests** (`backend/tests/test_resurrection_pipeline.py`, `backend/tests/test_mcp_server.py`, plus small additions to `test_no_float.py`-style scanners):
- Endpoint happy path per Preset (all 11 response keys present, HTTP 200).
- `preset_id` null for Custom_COBOL; both-fields-present 4xx; neither-field 4xx.
- Demo mode wiring: attempt 1 code originates from `FLOAT_BUG_REGISTRY`, attempt 2 from `REGISTRY`.
- Demo mode on Custom_COBOL → 4xx.
- Healing-failure scenario with a deterministic always-failing stub → 3 records, `status=failed`.
- Stale cached code failing fresh verification → `certified=false` with HTTP 200.
- `GET /api/export/{preset_id}` happy path and unknown-id 404; LLM/compiler untouched (patched to raise if called).
- `GET /health` untouched by the new code paths (LLM client, cache, compiler all patched to raise).
- MCP server: `initialize`, `tools/list`, one happy-path call per tool, unknown method (-32601), malformed JSON line (-32700, loop continues).
- Static scans: no heavy ML packages in `requirements.txt`; no secrets in hooks/MCP config/Dockerfiles/`.env.example`; `.env.example` has all 6 placeholder keys; devcontainer references `backend/Dockerfile`, forwards port 8000, has no secrets; hook files are valid JSON with argument-safe commands.

**Property tests** (Hypothesis, ≥100 iterations each, using a deterministic `LLMClient` stub so no network call or token cost occurs — satisfying Req 12.2):
- Each property above gets one `hypothesis`-driven test in `test_resurrection_pipeline.py`, tagged **Feature: resurrection-pipeline, Property N: <title>**.
- Generators: random valid/invalid `field_schema` lists; random `num_cases` across and outside [150,200]; random cobol snippets with an injected banned token at a random position; random pass/fail/parity-score sequences fed through the stub LLM to drive the self-healing loop's branches; random cache-insertion/lookup sequences (length > 64) compared against a reference `OrderedDict` LRU model (Property 25); random concurrent request batches with an instrumented `CobolRunner` recording enter/exit timestamps (Property 32).
- The stub `LLMClient` is a test double implementing the same three methods (`extract_rules`, `generate_python`, `repair_python`) returning scripted or randomized `ParsedRules`/`ArchitectOutput` values, so no `openai` network calls occur and `BudgetTracker.record` is driven with synthetic costs for Property 30.
