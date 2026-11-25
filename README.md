# VeriCOBOL

Resurrect any COBOL program into an exact-precision Python API and a live cloud dashboard, with penny-exact proof against the real GnuCOBOL compiler.

Built for the Kiroween 2026 Hackathon (Resurrection category).

## What it does

Paste, edit, or upload any COBOL program (`.cbl`, `.cob`, `.txt`). There are no hardcoded programs and no pre-baked results. Every program, including the three optional Starter Templates (Loan Amortization, Compound Interest, Daily Ledger), goes through the same live pipeline:

1. The sandbox validator rejects hostile source before any compile or LLM call.
2. Three agents infer, translate, and verify the program.
3. The UI renders a dashboard built from the inferred contract, and every slider change re-runs both the generated Python and the real COBOL binary.

## Architecture

```
COBOL source ──> Sandbox validator ──> Legacy Parser ──> Cloud Architect ──> Parity Auditor
 (paste/upload)   (400/413 on reject)   (LLM, stage 1)    (LLM, stage 2)      (live GnuCOBOL PBT)
                                              │                  │                   │
                                  contract, I/O protocol,   exact-Decimal       150 batched cases
                                  business rules,           Python 3.12         (35-50 for single-run
                                  dashboard metadata        calculate(**in)      programs), self-healing
                                                                                     │
                       Next.js dashboard <── FastAPI /api/resurrect, /api/calculate-custom, /api/export-custom
```

### Three-agent pipeline

| Stage | Agent | Job |
|---|---|---|
| 1 | Legacy Parser | Reads raw COBOL and infers the title, description, I/O protocol (`batch_space`, `single_run_lines`, `single_run_space`, `no_input_static`), input and output contract (PIC, scale, min, max, rounding, format), plain-English business rules, and dashboard metadata. |
| 2 | Cloud Architect | Generates standalone Python 3.12 that defines `calculate(**inputs) -> dict` using `decimal.Decimal` with an explicit context. COBOL stores without `ROUNDED` become `ROUND_DOWN`, stores with `ROUNDED` become `ROUND_HALF_UP`. |
| 3 | Parity Auditor | Compiles the original COBOL once with `cobc`, runs randomized adversarial cases against the binary and the generated Python, and requires a difference of exactly 0 in every output field. On a mismatch it sends the first counterexample back to the Cloud Architect (at most 2 retries). |

Verification always runs live against GnuCOBOL, including on pipeline-cache hits. The cache only saves LLM tokens. If the LLM is unavailable (missing key, provider failure, or budget exhausted), the API returns HTTP 503. It never falls back to pre-built results.

## Proof: IEEE-754 float drift

A naive float translation of COBOL money math looks right until a tiny value meets a large one:

```python
>>> -0.01 + 50_000_000 - 50_000_000
0.0                       # float: the cent is gone
```

```
GnuCOBOL (PIC S9(9)V99):  -0.01
```

In float, `-0.01 + 50,000,000` rounds to `49,999,999.99` at the limit of double precision, and subtracting 50,000,000 does not recover the original cent, so the result is `0.00`. GnuCOBOL does fixed-point decimal arithmetic and returns `-0.01`.

Turn on **demonstrate self-healing** and the pipeline runs this on your own program. Attempt 1 is a deliberate float translation. The Parity Auditor catches the drift with a concrete counterexample, feeds it to the Cloud Architect, and attempt 2 produces exact `Decimal` code that re-verifies at 100% parity. The audit trail shows both attempts.

## Using Kiro

| Path | How it is used |
|---|---|
| `.kiro/steering/` | Always-on project rules: product vision, tech stack, repository structure, financial rules (no floats, COBOL rounding semantics, compile-once PBT, sandbox rules), and frontend design (dark fintech theme, 3270 legacy panel colors). |
| `.kiro/agents/` | Custom agents `legacy-parser`, `cloud-architect`, and `parity-auditor`, one per pipeline stage. |
| `.kiro/specs/` | Spec-driven development: `cobol-verification-engine`, `resurrection-pipeline`, `mainframe-to-cloud-ui`, and `universal-open-cobol-platform`, each with requirements, design, and tasks. |
| `.kiro/hooks/` | `guard-no-float` blocks float money math. `verify-cobol-parity` runs the parity tests. |
| `.kiro/settings/mcp.json` | Local MCP server `vericobol-gnucobol`, run through Docker. It exposes tools to compile and run COBOL, list presets, and verify parity from inside Kiro. It holds no secrets. |
| `.devcontainer/` | Linux, Python 3.12, and GnuCOBOL from `backend/Dockerfile`, so Windows developers can run `cobc` inside the IDE. |

## Quick start

Prerequisites: Docker, Node.js 18+, and an OpenAI-compatible LLM endpoint.

### 1. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL` (default `gpt-6-luna`). Optional: `LLM_BUDGET_USD` (default `2.00`), `FRONTEND_URL`, `COBC_PATH` (default `cobc`). `.env` is git-ignored. Never commit it.

### 2. Backend (Docker)

```bash
docker build -t vericobol-backend ./backend
docker run --rm -p 8000:8000 --env-file .env vericobol-backend
```

Check it with `curl http://localhost:8000/health`. The response reports the compiler version and whether the LLM is configured (a boolean only, never the key).

Run the tests inside the container:

```bash
docker run --rm vericobol-backend python -m pytest
```

The tests use a mocked LLM, so they spend no tokens.

### 3. Frontend (Next.js)

```bash
cd frontend
cp .env.local.example .env.local   # NEXT_PUBLIC_API_URL=http://localhost:8000
npm install
npm run dev
```

Open http://localhost:3000.

## API

| Endpoint | Purpose |
|---|---|
| `GET /health` | Cheap status check. It does not call the LLM or the compiler. |
| `GET /api/presets` | Raw text of the three Starter Templates. |
| `POST /api/resurrect` | Run the full pipeline on `cobol_code` (or `preset_id`). Options: `num_cases`, `demonstrate_self_healing`, `force_live_llm`. |
| `POST /api/calculate-custom` | Run the generated Python and the real COBOL binary on one input set and report `parity_verified`. |
| `POST /api/export-custom` | Return the verified Python and a standalone FastAPI service. |

## Safety

- User COBOL is validated before any compile or LLM call. `CALL`, file I/O, `COPY`/`REPLACE`, and environment or command-line access return HTTP 400. The UI includes a "Load Test Sandbox Attack (CALL SYSTEM)" button to try it.
- Compiles and runs happen in a fresh temporary directory with a 5 s timeout and capped output.
- Money values are `Decimal` or strings end to end. No floats appear in the backend or the frontend.
- LLM spend is capped at $2 by default.

## License

See [LICENSE](LICENSE).
