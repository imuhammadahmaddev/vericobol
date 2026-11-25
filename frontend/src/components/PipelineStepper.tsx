import type { StageName, StageRecord, StageStatus } from "@/lib/types";

/** Fixed stage order and labels (Requirement 7.1). */
export const PIPELINE_STAGES: ReadonlyArray<{ name: StageName; label: string }> = [
  { name: "legacy_parser", label: "Legacy Parser" },
  { name: "cloud_architect", label: "Cloud Architect" },
  { name: "parity_auditor", label: "Parity Auditor" },
];

export interface PipelineStepperProps {
  /** `stages` from the /api/resurrect response; null/undefined when none yet. */
  stages: StageRecord[] | null | undefined;
  /** True while a /api/resurrect request is in flight. */
  loading: boolean;
  /** Stage currently running (from `useResurrect`), shown by name while loading. */
  currentStage?: StageName | null;
}

type ViewState =
  | { kind: "pending" }
  | { kind: "not_run" }
  | { kind: "done"; record: StageRecord };

function resolve(
  name: StageName,
  stages: StageRecord[] | null | undefined,
  loading: boolean,
): ViewState {
  if (loading) return { kind: "pending" };
  const record = stages?.find((s) => s.name === name);
  return record ? { kind: "done", record } : { kind: "not_run" };
}

function statusClasses(status: StageStatus): string {
  switch (status) {
    case "ok":
      return "border-[#10B981] text-[#10B981]";
    case "failed":
      return "border-[#EF4444] text-[#EF4444]";
    case "skipped":
      return "border-slate-700 text-slate-400";
  }
}

export default function PipelineStepper({ stages, loading, currentStage = null }: PipelineStepperProps) {
  const currentLabel = PIPELINE_STAGES.find((s) => s.name === currentStage)?.label ?? null;
  return (
    <div className="space-y-2">
      <ol
        aria-label="Modernization pipeline"
        aria-busy={loading}
        data-testid="pipeline-stepper"
        className="grid grid-cols-1 gap-3 rounded-xl border border-slate-800 bg-[#0F172A] p-3 sm:grid-cols-3"
      >
        {PIPELINE_STAGES.map(({ name, label }, i) => {
          const view = resolve(name, stages, loading);
          const isCurrent = loading && name === currentStage;
          const border =
            view.kind === "done"
              ? statusClasses(view.record.status)
              : isCurrent
                ? "border-[#10B981] text-slate-100"
                : "border-slate-700 text-slate-400";
          const isAuditor = name === "parity_auditor";
          return (
            <li
              key={name}
              data-testid={`stage-${name}`}
              data-state={view.kind === "done" ? view.record.status : view.kind}
              className={`rounded-xl border bg-[#1E293B] p-3 ${border} ${
                isCurrent ? "animate-pulse" : ""
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-semibold text-slate-100">
                  {i + 1}. {label}
                </span>
                {isAuditor ? (
                  <span
                    data-testid="stage-parity_auditor-source"
                    className="rounded-full border border-[#10B981] bg-[#10B981]/10 px-2 py-0.5 text-xs font-semibold text-[#10B981]"
                  >
                    live
                  </span>
                ) : (
                  view.kind === "done" && (
                    <span
                      data-testid={`stage-${name}-source`}
                      className="rounded-full border border-slate-700 px-2 py-0.5 text-xs text-slate-400"
                    >
                      {view.record.source}
                    </span>
                  )
                )}
              </div>
              <div className="mt-2 text-xs">
                {view.kind === "pending" && <span>{isCurrent ? "Running…" : "Pending…"}</span>}
                {view.kind === "not_run" && <span>not run</span>}
                {view.kind === "done" && (
                  <>
                    <span className="font-medium">{view.record.status}</span>
                    <span className="text-slate-400" data-testid={`stage-${name}-elapsed`}>
                      {" · "}
                      {view.record.elapsed_ms} ms
                    </span>
                  </>
                )}
              </div>
            </li>
          );
        })}
      </ol>
      {loading && currentLabel !== null && (
        <p role="status" data-testid="current-stage" className="text-sm text-slate-400">
          Running stage: <span className="font-semibold text-slate-100">{currentLabel}</span>
        </p>
      )}
    </div>
  );
}
