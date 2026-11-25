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
      return "border-slate-400 text-slate-500";
  }
}

export default function PipelineStepper({ stages, loading }: PipelineStepperProps) {
  return (
    <ol
      aria-label="Modernization pipeline"
      aria-busy={loading}
      data-testid="pipeline-stepper"
      className="grid grid-cols-1 gap-3 sm:grid-cols-3"
    >
      {PIPELINE_STAGES.map(({ name, label }, i) => {
        const view = resolve(name, stages, loading);
        const border =
          view.kind === "done"
            ? statusClasses(view.record.status)
            : "border-slate-300 text-slate-500";
        return (
          <li
            key={name}
            data-testid={`stage-${name}`}
            data-state={view.kind === "done" ? view.record.status : view.kind}
            className={`rounded-xl border bg-white p-3 ${border} ${
              view.kind === "pending" ? "animate-pulse" : ""
            }`}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-semibold text-slate-900">
                {i + 1}. {label}
              </span>
              {view.kind === "done" && (
                <span
                  data-testid={`stage-${name}-source`}
                  className="rounded-full border border-slate-300 px-2 py-0.5 text-xs text-slate-700"
                >
                  {view.record.source}
                </span>
              )}
            </div>
            <div className="mt-2 text-xs">
              {view.kind === "pending" && <span>Pending…</span>}
              {view.kind === "not_run" && <span>not run</span>}
              {view.kind === "done" && (
                <>
                  <span className="font-medium">{view.record.status}</span>
                  <span className="text-slate-600" data-testid={`stage-${name}-elapsed`}>
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
  );
}
