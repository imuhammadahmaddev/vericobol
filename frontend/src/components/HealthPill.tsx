import { formatHealthLabel } from "@/hooks/useHealth";
import type { HealthResponse } from "@/lib/types";

export type HealthPillState = "online" | "unavailable" | "connecting";

/** Pure derivation of the pill state from the health response. */
export function deriveHealthPillState(
  health: HealthResponse | null | undefined,
): HealthPillState {
  if (!health || health.status !== "ok") return "connecting";
  if (health.cobc_available === false) return "unavailable";
  return "online";
}

/** "cobc (GnuCOBOL) 3.2.0" -> "GnuCOBOL 3.2.0 Online". */
export function healthPillLabel(
  health: HealthResponse | null | undefined,
): string {
  const state = deriveHealthPillState(health);
  if (state === "connecting") return "Connecting...";
  if (state === "unavailable") return "GnuCOBOL unavailable";
  return formatHealthLabel(health?.cobc_version ?? "", true);
}

interface HealthPillProps {
  health: HealthResponse | null | undefined;
}

export default function HealthPill({ health }: HealthPillProps) {
  const state = deriveHealthPillState(health);
  const color =
    state === "online"
      ? "border-verified text-verified"
      : state === "unavailable"
        ? "border-bug text-bug"
        : "border-slate-700 text-slate-400";
  const dot =
    state === "online"
      ? "bg-verified"
      : state === "unavailable"
        ? "bg-bug"
        : "bg-slate-500 animate-pulse";

  return (
    <span
      role="status"
      data-testid="health-pill"
      data-state={state}
      className={`inline-flex items-center gap-2 whitespace-nowrap rounded-full border bg-[#0F172A] px-3 py-1 text-xs font-medium ${color}`}
    >
      <span className={`h-2 w-2 rounded-full ${dot}`} aria-hidden="true" />
      {healthPillLabel(health)}
    </span>
  );
}
