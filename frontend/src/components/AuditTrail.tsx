import type {
  AttemptOutcome,
  AttemptRecord,
  Counterexample,
  FinalReport,
  LikelyCause,
} from "@/lib/types";

/**
 * Derive an attempt's outcome (design Discrepancy 7).
 * - last entry and final status "passed" -> "passed"
 * - last entry otherwise -> "failed"
 * - earlier entry (a later attempt exists) -> "repaired"
 */
export function deriveAttemptOutcome(
  attempt: AttemptRecord,
  index: number,
  total: number,
  finalStatus: FinalReport["status"],
): AttemptOutcome {
  void attempt;
  if (index === total - 1) return finalStatus === "passed" ? "passed" : "failed";
  return "repaired";
}

const CAUSE_LABEL: Record<LikelyCause, string> = {
  float_drift: "Float Drift",
  rounded_vs_truncation: "ROUNDED vs Truncation",
  scale_mismatch: "Scale Mismatch",
  overflow: "Overflow",
  python_error: "Python Error",
  unknown: "Unknown",
};

function causeLabel(cause: string): string {
  return CAUSE_LABEL[cause as LikelyCause] ?? cause;
}

/** Render a field map as raw "name=value" pairs, values verbatim. */
function fmt(map: Record<string, string>): string {
  return Object.entries(map)
    .map(([k, v]) => `${k}=${v}`)
    .join(", ");
}

export interface AuditTrailProps {
  /** `audit_trail` from the /api/resurrect response. */
  attempts: AttemptRecord[] | null | undefined;
  /** `final_report` from the /api/resurrect response. */
  finalReport: FinalReport | null | undefined;
}

interface Row {
  key: string;
  attempt: string;
  ce: Counterexample;
}

function headline(
  outcome: AttemptOutcome,
  index: number,
  a: AttemptRecord,
): { text: string; tone: string } {
  if (outcome === "passed") {
    return {
      text:
        index > 0
          ? `SELF-HEALED — Exact Decimal ${a.parity_score_pct}% Certified`
          : `PASSED — Exact Decimal ${a.parity_score_pct}% Certified`,
      tone: "border-[#10B981] bg-[#10B981]/10 text-[#10B981]",
    };
  }
  const cause = a.counterexample
    ? `${causeLabel(a.counterexample.likely_cause)} Detected`
    : "Mismatch Detected";
  return {
    text: `FAILED — ${cause}`,
    tone: "border-[#EF4444] bg-[#EF4444]/10 text-[#EF4444]",
  };
}

/** Self-healing audit trail and counterexample diff log (Requirements 13.1-13.5). */
export default function AuditTrail({ attempts, finalReport }: AuditTrailProps) {
  const list = attempts ?? [];
  const finalStatus = finalReport?.status ?? "failed";

  const rows: Row[] = [];
  list.forEach((a) => {
    if (a.counterexample) {
      rows.push({
        key: `a${a.attempt}`,
        attempt: String(a.attempt),
        ce: a.counterexample,
      });
    }
  });
  if (rows.length === 0 && finalReport) {
    finalReport.counterexamples.forEach((ce, i) =>
      rows.push({ key: `f${i}`, attempt: "final", ce }),
    );
  }

  return (
    <section
      data-testid="audit-trail"
      aria-label="Self-healing audit trail"
      className="w-full rounded-lg border border-slate-200 bg-white p-4 text-slate-900"
    >
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-600">
        Audit Trail
      </h2>

      {list.length > 0 && (
        <ol className="mb-4 grid gap-3 md:grid-cols-2">
          {list.map((a, i) => {
            const outcome = deriveAttemptOutcome(a, i, list.length, finalStatus);
            const h = headline(outcome, i, a);
            return (
              <li
                key={`${a.attempt}-${i}`}
                data-testid="audit-attempt"
                data-outcome={outcome}
                className={`rounded-md border-2 px-3 py-2 ${h.tone}`}
              >
                <p className="font-bold">
                  Attempt {a.attempt}: {h.text}
                </p>
                <p className="text-sm">
                  {a.passed_cases} / {a.total_cases} cases · {a.parity_score_pct}%
                </p>
              </li>
            );
          })}
        </ol>
      )}

      {rows.length === 0 ? (
        <p data-testid="audit-empty" className="text-sm text-slate-600">
          No discrepancies found; no repair needed
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table
            data-testid="audit-diff-table"
            className="w-full border-collapse text-left font-mono text-xs"
          >
            <thead>
              <tr className="border-b border-slate-300 text-slate-600">
                <th className="px-2 py-1">Attempt</th>
                <th className="px-2 py-1">Inputs</th>
                <th className="px-2 py-1">GnuCOBOL Exact</th>
                <th className="px-2 py-1">Python Output</th>
                <th className="px-2 py-1">Differing Field</th>
                <th className="px-2 py-1">Penny Delta</th>
                <th className="px-2 py-1">Likely Cause</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.key}
                  data-testid="audit-diff-row"
                  className="border-b border-slate-100 align-top"
                >
                  <td className="px-2 py-1">{r.attempt}</td>
                  <td className="px-2 py-1 break-all">{fmt(r.ce.inputs)}</td>
                  <td className="px-2 py-1 break-all">{fmt(r.ce.cobol_output)}</td>
                  <td className="px-2 py-1 break-all text-[#EF4444]">
                    {fmt(r.ce.python_output)}
                  </td>
                  <td className="px-2 py-1">{r.ce.differing_field}</td>
                  <td className="px-2 py-1">{r.ce.penny_delta}</td>
                  <td className="px-2 py-1">
                    <span
                      data-testid="cause-badge"
                      data-cause={r.ce.likely_cause}
                      className="inline-block rounded-full border border-[#EF4444] bg-[#EF4444]/10 px-2 py-0.5 font-sans font-semibold text-[#EF4444]"
                    >
                      {causeLabel(r.ce.likely_cause)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
