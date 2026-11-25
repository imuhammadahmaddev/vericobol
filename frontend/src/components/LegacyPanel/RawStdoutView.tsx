"use client";

const FONT =
  'var(--font-jetbrains-mono), "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
const GLOW = "0 0 6px rgba(0,255,102,0.55)";
const MAX_SAMPLE_LINES = 20;

interface RawStdoutViewProps {
  /** `raw_cobol_output` from the latest /api/calculate response. */
  liveOutput?: string | null;
  /** `raw_cobol_sample_output` from the verification report (batch). */
  batchSample?: string[] | null;
}

export default function RawStdoutView({
  liveOutput,
  batchSample,
}: RawStdoutViewProps) {
  const sample = (batchSample ?? []).slice(0, MAX_SAMPLE_LINES);
  const hasLive = liveOutput != null && liveOutput !== "";

  return (
    <section
      aria-label="Raw GnuCOBOL STDOUT"
      data-testid="raw-stdout-view"
      className="bg-[#0A0E0C] text-[#00FF66]"
      style={{ fontFamily: FONT, textShadow: GLOW }}
    >
      <div className="border-b border-[#00FF66]/30 px-3 py-2 text-xs uppercase tracking-widest">
        GNUCOBOL STDOUT
      </div>
      <div className="space-y-3 px-3 py-2 text-sm leading-5">
        <div>
          <div className="text-xs text-[#00FF66]/60">&gt; LIVE CALCULATE</div>
          <pre
            data-testid="stdout-live"
            className="overflow-auto whitespace-pre-wrap break-all"
          >
            {hasLive ? liveOutput : "(no output yet)"}
            <span
              aria-hidden="true"
              className="ml-1 inline-block h-4 w-2 translate-y-0.5 animate-pulse bg-[#00FF66]"
            />
          </pre>
        </div>
        <div>
          <div className="text-xs text-[#00FF66]/60">
            &gt; BATCH SAMPLE ({sample.length}/{MAX_SAMPLE_LINES})
          </div>
          <pre
            data-testid="stdout-batch"
            className="max-h-56 overflow-auto whitespace-pre-wrap break-all"
          >
            {sample.length > 0 ? sample.join("\n") : "(run Resurrect to see batch output)"}
          </pre>
        </div>
      </div>
    </section>
  );
}
