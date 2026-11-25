---
inclusion: always
---

# VeriCOBOL: Technology Stack

## Frontend
- Next.js (App Router), TypeScript, Tailwind CSS.
- Deployed on Vercel Hobby Tier ($0).
- API base URL from `NEXT_PUBLIC_API_URL`.

## Backend
- Python 3.12, FastAPI, Uvicorn, `gnucobol` (`cobc`), `hypothesis`, `openai` SDK.
- Docker image based on `python:3.12-slim`, deployed on Render Free Tier ($0).
- Render limits: 512 MB RAM, 0.1 CPU. Target RAM under 200 MB. Cold start takes 30-60 s.
- Endpoint `GET /health` must be cheap and must not invoke the LLM or compiler.

## Banned Libraries
Do NOT install `pandas`, `numpy`, `scipy`, `torch`, or any heavy ML framework. Use the standard library (`decimal`, `json`, `hashlib`, `subprocess`, `pathlib`, `tempfile`).

## Storage and Caching
- No external database. Presets are static JSON/COBOL files.
- In-memory SHA-256 hash cache keyed on the normalized COBOL source.
- Pre-verified cached pipeline results are bundled for the 3 presets, so repeated clicks cost $0 in LLM tokens. Live GnuCOBOL PBT verification still runs every time.

## LLM Client
- OpenAI-compatible client configured only through env vars: `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`.
- Local dev may use $0 providers (Groq, Gemini). Production uses a budget model (`gpt-6-luna`). Total LLM budget: $2.
- Never hardcode keys or endpoints. `.env.example` holds placeholders only.

## Other Env Vars
`FRONTEND_URL` (CORS allow-list), `NEXT_PUBLIC_API_URL`, `COBC_PATH` (default `cobc`).

## Performance
Verification of one program (150-200 cases) must finish in under 3 seconds on 0.1 CPU. Compile once, batch all cases (see financial-rules.md).

## Cross-Platform
- Use `pathlib.Path` everywhere. No hardcoded path separators.
- The developer is on Windows. GnuCOBOL runs on Linux through `.devcontainer/devcontainer.json` inside Kiro IDE.
