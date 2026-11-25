"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import AuditTrail from "@/components/AuditTrail";
import CloudDashboard from "@/components/CloudDashboard/CloudDashboard";
import ColdStartBanner from "@/components/ColdStartBanner";
import HealthPill from "@/components/HealthPill";
import LegacyPanel from "@/components/LegacyPanel/LegacyPanel";
import ParityBanner from "@/components/ParityBanner";
import PipelineStepper from "@/components/PipelineStepper";
import PresetTabs from "@/components/PresetTabs";
import { genericErrorMessage } from "@/components/RejectionDetail";
import { useHealth } from "@/hooks/useHealth";
import { useResurrect } from "@/hooks/useResurrect";
import { api } from "@/lib/api";
import { SANDBOX_TEST_SNIPPET } from "@/lib/sandboxSnippet";
import {
  ApiError,
  type FieldContractSpecBody,
  type PresetId,
  type PresetSummary,
  type ResurrectRequestBody,
  type ResurrectResponse,
} from "@/lib/types";

const NUM_CASES = 150;
const DEFAULT_PRESET: PresetId = "loan_calc";

/** Build the custom-COBOL field contract from the active preset's input schema and the last PIC map. */
function buildFieldSchema(
  preset: PresetSummary,
  last: ResurrectResponse | null,
): FieldContractSpecBody[] | undefined {
  if (!last || last.pic_mappings.length === 0) return undefined;
  const inputs = new Map(preset.input_schema.map((f) => [f.name, f]));
  return last.pic_mappings.map((m) => {
    const inp = inputs.get(m.name);
    const spec: FieldContractSpecBody = {
      name: m.name,
      pic: m.pic,
      direction: inp ? "input" : "output",
      scale: parseInt(m.scale, 10),
      rounding: m.rounding,
    };
    if (inp) {
      spec.min = inp.min;
      spec.max = inp.max;
      spec.step = inp.step;
      spec.label = inp.label;
    }
    return spec;
  });
}

export default function Page() {
  const { state: healthState, health, attempts } = useHealth();
  const healthy = health !== null;

  const [presets, setPresets] = useState<PresetSummary[] | null>(null);
  const [presetsError, setPresetsError] = useState<string | null>(null);
  const [presetsLoading, setPresetsLoading] = useState(false);
  const [selectedId, setSelectedId] = useState<PresetId | null>(null);
  const [source, setSource] = useState("");
  const [edited, setEdited] = useState(false);
  const [liveOutput, setLiveOutput] = useState<string | null>(null);

  const { run, loading, data, error } = useResurrect();
  const autoRan = useRef(false);

  const preset = useMemo(
    () => presets?.find((p) => p.preset_id === selectedId) ?? null,
    [presets, selectedId],
  );

  const loadPresets = useCallback(() => {
    setPresetsLoading(true);
    setPresetsError(null);
    api
      .presets()
      .then((list) => setPresets(list))
      .catch((e: unknown) =>
        setPresetsError(
          `Could not load presets from GET /api/presets: ${genericErrorMessage(e)}`,
        ),
      )
      .finally(() => setPresetsLoading(false));
  }, []);

  useEffect(() => {
    if (healthy && presets === null) loadPresets();
  }, [healthy, presets, loadPresets]);

  const resurrect = useCallback(
    (target: PresetSummary, selfHeal: boolean, useEditedSource: boolean, src: string) => {
      setLiveOutput(null);
      const body: ResurrectRequestBody = {
        num_cases: NUM_CASES,
        demonstrate_self_healing: selfHeal,
      };
      if (useEditedSource) {
        body.cobol_code = src;
        const schema = buildFieldSchema(target, data);
        if (schema) body.field_schema = schema;
      } else {
        body.preset_id = target.preset_id;
      }
      void run(body);
    },
    [run, data],
  );

  // Auto-run loan_calc once presets have loaded.
  useEffect(() => {
    if (autoRan.current || !healthy || !presets) return;
    const first = presets.find((p) => p.preset_id === DEFAULT_PRESET) ?? presets[0];
    if (!first) return;
    autoRan.current = true;
    setSelectedId(first.preset_id);
    setSource(first.cobol_source);
    setEdited(false);
    resurrect(first, false, false, first.cobol_source);
  }, [healthy, presets, resurrect]);

  const onSelect = (id: PresetId) => {
    const next = presets?.find((p) => p.preset_id === id);
    if (!next) return;
    setSelectedId(id);
    setSource(next.cobol_source);
    setEdited(false);
    resurrect(next, false, false, next.cobol_source);
  };

  const onSourceChange = (s: string) => {
    setSource(s);
    setEdited(preset ? s !== preset.cobol_source : true);
  };

  const canRun = healthy && !loading && preset !== null;
  const onRun = (selfHeal: boolean) => {
    if (preset) resurrect(preset, selfHeal, edited, source);
  };

  const apiError = error instanceof ApiError ? error : null;
  const isRejection = apiError?.status === 400;
  const showPageError = apiError !== null && !isRejection;

  const report = data?.final_report ?? null;

  return (
    <main className="min-h-screen">
      {!healthy && <ColdStartBanner />}
      {!healthy && attempts >= 15 && (
        <p role="status" className="bg-[#0A0E0C] px-4 py-2 text-sm text-[#00FF66]">
          Still warming up the free-tier server. This can take a minute. Retrying automatically...
        </p>
      )}

      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[#00FF66]/40 bg-[#0A0E0C] px-4 py-3">
        <div>
          <h1 className="text-2xl font-bold tracking-wide text-[#00FF66]">VeriCOBOL</h1>
          <p className="text-sm text-[#00FF66]/80">1959 COBOL → 2026 Verified Cloud</p>
        </div>
        <HealthPill health={health} />
      </header>

      <div className="space-y-4 p-4">
        <PresetTabs
          presets={presets}
          selectedId={selectedId}
          onSelect={onSelect}
          error={presetsError}
          onRetry={loadPresets}
          loading={presetsLoading}
        />

        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            disabled={!canRun}
            onClick={() => onRun(false)}
            className="rounded border border-[#00FF66] bg-[#0A0E0C] px-4 py-2 text-sm font-semibold text-[#00FF66] hover:bg-[#00FF66]/10 focus:outline-none focus:ring-2 focus:ring-[#00FF66] disabled:cursor-not-allowed disabled:opacity-40"
          >
            Resurrect &amp; Verify
          </button>
          <button
            type="button"
            disabled={!canRun}
            onClick={() => onRun(true)}
            className="rounded bg-[#EF4444] px-4 py-2 text-sm font-semibold text-white shadow-lg shadow-[#EF4444]/30 hover:bg-[#DC2626] focus:outline-none focus:ring-2 focus:ring-[#EF4444] focus:ring-offset-2 focus:ring-offset-[#0A0E0C] disabled:cursor-not-allowed disabled:opacity-40"
          >
            Demo: Catch Float Bug &amp; Self-Heal
          </button>
          {!healthy && (
            <span className="text-xs text-[#00FF66]/70">Actions unlock when the backend is online.</span>
          )}
          {healthy && healthState === "degraded" && (
            <span className="text-xs text-[#EF4444]">GnuCOBOL unavailable on the backend.</span>
          )}
        </div>

        <PipelineStepper stages={data?.stages} loading={loading} />

        {showPageError && apiError && (
          <div role="alert" className="rounded border border-[#EF4444] bg-[#0A0E0C] p-3 text-sm text-[#EF4444]">
            <p>
              Resurrect failed ({apiError.status || "network"}): {genericErrorMessage(apiError)}
            </p>
            {apiError.status === 503 && (
              <p className="mt-1 text-[#00FF66]">
                Note: the bundled presets need no LLM key. Select a preset instead of custom COBOL.
              </p>
            )}
          </div>
        )}

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <LegacyPanel
            source={source}
            onSourceChange={onSourceChange}
            edited={edited}
            onLoadSandboxSnippet={() => {
              setSource(SANDBOX_TEST_SNIPPET);
              setEdited(true);
            }}
            liveOutput={liveOutput}
            batchSample={data?.final_report.raw_cobol_sample_output ?? null}
            businessRules={data?.business_rules ?? []}
            picMappings={data?.pic_mappings ?? []}
            error={isRejection ? apiError : null}
            disabled={loading}
          />
          <CloudDashboard
            preset={preset}
            resurrect={data}
            onCalculated={(_res, raw) => setLiveOutput(raw)}
          />
        </div>

        <ParityBanner report={report} cacheHit={data?.cache_hit ?? false} />
        <AuditTrail attempts={data?.audit_trail} finalReport={report} />
      </div>
    </main>
  );
}
