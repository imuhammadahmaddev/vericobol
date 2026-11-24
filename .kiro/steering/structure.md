---
inclusion: always
---

# VeriCOBOL: Repository Structure

Monorepo layout:

```
vericobol/
├── .devcontainer/
│   └── devcontainer.json        # Linux + GnuCOBOL for Windows developers
├── .kiro/                       # NEVER gitignore; required for judging
│   ├── steering/                # always-on and fileMatch docs
│   ├── agents/                  # custom agents (Markdown)
│   ├── specs/                   # cobol-verification-engine + quick specs
│   ├── hooks/                   # JSON hooks guarding precision, running parity tests
│   └── settings/
│       └── mcp.json             # local GnuCOBOL MCP server, no secrets
├── frontend/                    # Next.js App Router, TypeScript, Tailwind
├── backend/
│   ├── app/                     # FastAPI app, pipeline, verification engine
│   ├── cobol_samples/           # loan_calc.cbl, compound_int.cbl, daily_ledger.cbl
│   ├── tests/                   # pytest + Hypothesis differential tests
│   ├── mcp_server/              # local GnuCOBOL MCP server
│   ├── Dockerfile               # python:3.12-slim + gnucobol
│   └── requirements.txt
├── .env.example
└── .gitignore
```

## Conventions
- Backend code lives under `backend/app/`. Tests live under `backend/tests/`.
- Preset COBOL files and their cached verified results live in `backend/cobol_samples/`.
- Use `pathlib.Path` for all paths.
- Secrets only in `.env` (ignored). Never commit them.
