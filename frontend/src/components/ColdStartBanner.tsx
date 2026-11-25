export const COLD_START_TEXT =
  "ESTABLISHING 3270 MAINFRAME CONNECTION... WARMING UP GNUCOBOL COMPILER...";

/** Themed warm-up status. Render it only until `/health` succeeds. */
export default function ColdStartBanner() {
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="cold-start-banner"
      className="w-full border-b border-phosphor bg-mainframe-bg px-4 py-2 font-mono3270 text-sm text-phosphor"
      style={{ textShadow: "0 0 6px #00FF66" }}
    >
      {COLD_START_TEXT}
      <span className="ml-1 inline-block animate-pulse" aria-hidden="true">
        █
      </span>
    </div>
  );
}
