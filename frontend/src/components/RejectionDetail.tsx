import { ApiError } from "@/lib/types";
import type { RejectedErrorDetail } from "@/lib/types";

/** Narrow an error to a sandbox rejection: status 400 and detail.error === "rejected". */
export function asRejection(error: unknown): RejectedErrorDetail | null {
  if (!(error instanceof ApiError) || error.status !== 400) return null;
  const detail: unknown = error.detail;
  if (
    typeof detail === "object" &&
    detail !== null &&
    !Array.isArray(detail) &&
    (detail as { error?: unknown }).error === "rejected"
  ) {
    return detail as RejectedErrorDetail;
  }
  return null;
}

/** Generic fallback text for non-rejection errors. */
export function genericErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const d: unknown = error.detail;
    if (typeof d === "object" && d !== null && !Array.isArray(d)) {
      const m = (d as { message?: unknown }).message;
      if (typeof m === "string") return m;
    }
    return String(d);
  }
  return error instanceof Error ? error.message : String(error);
}

interface RejectionDetailProps {
  error: unknown;
}

export default function RejectionDetail({ error }: RejectionDetailProps) {
  const rejection = asRejection(error);

  if (!rejection) {
    return (
      <div
        role="alert"
        className="border border-[#EF4444] bg-[#0A0E0C] p-3 font-mono text-sm text-[#EF4444]"
      >
        {genericErrorMessage(error)}
      </div>
    );
  }

  return (
    <div
      role="alert"
      data-testid="rejection-detail"
      className="border border-[#EF4444] bg-[#0A0E0C] p-3 font-mono text-sm text-[#EF4444]"
    >
      <p className="mb-2 font-bold uppercase tracking-wide">
        Sandbox rejected this program
      </p>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        <dt>STATUS</dt>
        <dd data-testid="rejection-status">400</dd>
        <dt>TOKEN</dt>
        <dd data-testid="rejection-token">{rejection.token}</dd>
        <dt>LINE</dt>
        <dd data-testid="rejection-line">{rejection.line}</dd>
        <dt>MESSAGE</dt>
        <dd data-testid="rejection-message">{rejection.message}</dd>
      </dl>
    </div>
  );
}
