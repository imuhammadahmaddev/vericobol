#!/usr/bin/env node
// Backend smoke check (Req 16). Plain Node ESM, Node 22 fetch, no dependencies.
//
// Usage:
//   npm run smoke -- [baseUrl] [--docker-skipped]
//
// Base URL precedence: first positional argument, SMOKE_API_URL,
// NEXT_PUBLIC_API_URL, then http://localhost:8000.
// Pass --docker-skipped when the backend is a local Uvicorn process because
// Docker was unavailable; the script then says so in its report.

const ORIGIN = "http://localhost:3000";
const TIMEOUT_MS = 60_000;

const args = process.argv.slice(2);
const dockerSkipped = args.includes("--docker-skipped");
const positional = args.filter((a) => !a.startsWith("--"));
const base = (
  positional[0] ||
  process.env.SMOKE_API_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  "http://localhost:8000"
).replace(/\/+$/, "");

const SANDBOX_SNIPPET = `       IDENTIFICATION DIVISION.
       PROGRAM-ID. SANDBOX-TEST.
       PROCEDURE DIVISION.
           CALL "SYSTEM" USING "echo pwned".
           STOP RUN.
`;

const LOAN_SCHEMA = [
  { name: "principal", pic: "9(9)V99", direction: "input", scale: 2, min: "0.00", max: "1000000.00", step: "0.01" },
  { name: "annual_rate_pct", pic: "9(3)V999", direction: "input", scale: 3, min: "0.000", max: "100.000", step: "0.001" },
  { name: "term_months", pic: "9(3)", direction: "input", scale: 0, min: "1", max: "600", step: "1" },
  { name: "monthly_payment", pic: "9(9)V99", direction: "output", scale: 2 },
  { name: "total_interest", pic: "9(9)V99", direction: "output", scale: 2 },
  { name: "total_payable", pic: "9(9)V99", direction: "output", scale: 2 },
];

async function call(method, path, body) {
  const headers = { Origin: ORIGIN };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(`${base}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  const text = await res.text();
  let json = null;
  try {
    json = JSON.parse(text);
  } catch {
    // leave json null; the check reports the raw status
  }
  return { status: res.status, headers: res.headers, json, text };
}

function corsOk(res) {
  const allow = res.headers.get("access-control-allow-origin");
  return allow === ORIGIN || allow === "*";
}

const checks = [
  {
    name: "GET /health returns 200 with status ok (CORS for " + ORIGIN + ")",
    run: async () => {
      const r = await call("GET", "/health");
      if (r.status !== 200) return `status ${r.status}`;
      if (r.json?.status !== "ok") return `status field was ${JSON.stringify(r.json?.status)}`;
      if (!corsOk(r)) return `Access-Control-Allow-Origin was ${r.headers.get("access-control-allow-origin")}`;
      return null;
    },
  },
  {
    name: "POST /api/resurrect loan_calc (150 cases) returns 200, zero mismatches (CORS)",
    run: async () => {
      const r = await call("POST", "/api/resurrect", { preset_id: "loan_calc", num_cases: 150 });
      if (r.status !== 200) return `status ${r.status}: ${r.text.slice(0, 200)}`;
      const rep = r.json?.final_report;
      if (!rep) return "missing final_report";
      if (rep.failed_cases !== 0) return `failed_cases ${rep.failed_cases}`;
      if (rep.passed_cases !== rep.total_cases || !(rep.total_cases >= 150)) {
        return `passed ${rep.passed_cases} of ${rep.total_cases}`;
      }
      if (Array.isArray(rep.counterexamples) && rep.counterexamples.length > 0) {
        return `${rep.counterexamples.length} counterexamples`;
      }
      if (!corsOk(r)) return `Access-Control-Allow-Origin was ${r.headers.get("access-control-allow-origin")}`;
      return null;
    },
  },
  {
    name: 'POST /api/resurrect with CALL "SYSTEM" returns 400, error rejected',
    run: async () => {
      const r = await call("POST", "/api/resurrect", {
        cobol_code: SANDBOX_SNIPPET,
        field_schema: LOAN_SCHEMA,
        num_cases: 150,
      });
      if (r.status !== 400) return `status ${r.status}: ${r.text.slice(0, 200)}`;
      const err = r.json?.detail?.error;
      if (err !== "rejected") return `detail.error was ${JSON.stringify(err)}`;
      return null;
    },
  },
  {
    name: "GET /api/export/loan_calc returns 200 with export_fastapi_code",
    run: async () => {
      const r = await call("GET", "/api/export/loan_calc");
      if (r.status !== 200) return `status ${r.status}`;
      const code = r.json?.export_fastapi_code;
      if (typeof code !== "string" || code.trim() === "") return "export_fastapi_code empty";
      return null;
    },
  },
];

console.log(`Smoke check against ${base}`);
console.log(
  dockerSkipped
    ? "Docker was skipped; using a locally started Uvicorn process."
    : "Target assumed to be the Docker image (pass --docker-skipped for local Uvicorn).",
);

let failures = 0;
for (const check of checks) {
  let problem;
  try {
    problem = await check.run();
  } catch (err) {
    problem = `request failed: ${err instanceof Error ? err.message : String(err)}`;
  }
  if (problem) {
    failures += 1;
    console.log(`FAIL  ${check.name}\n      ${problem}`);
  } else {
    console.log(`PASS  ${check.name}`);
  }
}

console.log(failures === 0 ? `All ${checks.length} checks passed.` : `${failures} of ${checks.length} checks failed.`);
process.exit(failures === 0 ? 0 : 1);
