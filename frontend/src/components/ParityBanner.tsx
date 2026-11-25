import type { FinalReport } from "@/lib/types";

export interface ParityBannerProps {
  /** `final_report` from the /api/resurrect response; null/undefined when none yet. */
  report: FinalReport | null | undefined;
  /** `cache_hit` from the /api/resurrect response. */
  cacheHit: boolean;
}

/** Full-width parity result banner (Requirements 12.1-12.5). */
export default function ParityBanner({ report, cacheHit }: ParityBannerProps) {
  if (!report) return null;

  const passing = report.failed_cases === 0;
  const certified = passing && report.certified;
  const ms = report.execution_ms;

  const tone = certified
    ? "border-[#10B981] bg-[#10B981]/10 text-[#10B981]"
    : "border-[#EF4444] bg-[#EF4444]/10 text-[#EF4444]";

  return (
    <section
      role="status"
      aria-live="polite"
      data-testid="parity-banner"
      data-state={certified ? "passing" : "failing"}
      className={`w-full rounded-lg border-2 px-4 py-3 ${tone}`}
    >
      {certified ? (
        <p className="text-base font-bold">
          {report.parity_score_pct}% MATHEMATICAL PARITY VERIFIED —{" "}
          {report.passed_cases} / {report.total_cases} Exact Penny Matches · 0
          Hallucinations · {ms}ms
        </p>
      ) : (
        <p className="text-base font-bold">
          PARITY NOT CERTIFIED — {report.passed_cases} / {report.total_cases}{" "}
          Exact Penny Matches · {report.failed_cases} mismatch
          {report.failed_cases === 1 ? "" : "es"} · {ms}ms
        </p>
      )}
      {cacheHit && (
        <p className="mt-1 text-sm font-normal" data-testid="parity-cache-note">
          LLM stages served from cache. Live GnuCOBOL verification ran on this
          request.
        </p>
      )}
    </section>
  );
}
