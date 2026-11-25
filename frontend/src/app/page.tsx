"use client";

import { useCallback, useEffect, useState } from "react";
import AuditTrail from "@/components/AuditTrail";
import CloudDashboard from "@/components/CloudDashboard/CloudDashboard";
import ExportTab from "@/components/CloudDashboard/ExportTab";
import ColdStartBanner from "@/components/ColdStartBanner";
import HealthPill from "@/components/HealthPill";
import LegacyPanel from "@/components/LegacyPanel/LegacyPanel";
import ParityBanner from "@/components/ParityBanner";
import PipelineStepper from "@/components/PipelineStepper";
import PresetTabs, { type StarterTemplate } from "@/components/PresetTabs";
import { genericErrorMessage } from "@/components/RejectionDetail";
import { useHealth } from "@/hooks/useHealth";
import { useResurrect } from "@/hooks/useResurrect";
import { api } from "@/lib/api";
import { ApiError, type ResurrectRequestBody } from "@/lib/types";

const NUM_CASES = 150;

export default function Page() {
  const { state: healthState, health, attempts } = useHealth();
  const healthy = health !== null;

  const [templates, setTemplates] = useState<StarterTemplate[] | null>(null);
  const [templatesError, setTemplatesError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [source, setSource] = useState("");
  const [forceLiveLlm, setForceLiveLlm] = useState(false);
  const [liveOutput, setLiveOutput] = useState<string | null>(null);
  /** The COBOL text that produced the current resurrect result. */
  const [resurrectedSource, setResurrectedSource] = useState("");

  const { run, loading, data, error, currentStage } = useResurrect();

  const loadTemplates = useCallback(() => {
    setTemplatesError(null);
    api
      .presets()
      .then((list) => setTemplates(list))
      .catch((e: unknown) =>
        setTemplatesError(genericErrorMessage(e)),
      );
  }, []);

  useEffect(() => {
    if (healthy && templates === null) loadTemplates();
  }, [healthy, templates, loadTemplates]);

  const onSelect = (template: StarterTemplate) => {
    setSelectedId(template.id);
    setSource(template.cobol_code);
  };

  const onCustom = () => {
    setSelectedId(null);
    setSource("");
  };

  const onCustomEdit = () => setSelectedId(null);

  const onStdout = useCallback((stdout: string) => setLiveOutput(stdout), []);

  const canRun = healthy && !loading && source.trim().length > 0;
  const onRun = (selfHeal: boolean) => {
    setLiveOutput(null);
    setResurrectedSource(source);
    const body: ResurrectRequestBody = {
      cobol_code: source,
      num_cases: NUM_CASES,
      demonstrate_self_healing: selfHeal,
      force_live_llm: forceLiveLlm,
    };
    void run(body);
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
          templates={templates}
          selectedId={selectedId}
          onSelect={onSelect}
          onCustom={onCustom}
          error={templatesError}
          onRetry={loadTemplates}
          disabled={loading}
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

        <PipelineStepper stages={data?.stages} loading={loading} currentStage={currentStage} />

        {showPageError && apiError && (
          <div role="alert" className="rounded border border-[#EF4444] bg-[#0A0E0C] p-3 text-sm text-[#EF4444]">
            <p>
              Resurrect failed ({apiError.status || "network"}): {genericErrorMessage(apiError)}
            </p>
          </div>
        )}

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <LegacyPanel
            source={source}
            onSourceChange={setSource}
            forceLiveLlm={forceLiveLlm}
            onForceLiveLlmChange={setForceLiveLlm}
            onCustomEdit={onCustomEdit}
            liveOutput={liveOutput}
            batchSample={data?.final_report.raw_cobol_sample_output ?? null}
            businessRules={data?.business_rules ?? []}
            picMappings={data?.pic_mappings ?? []}
            error={isRejection ? apiError : null}
            disabled={loading}
          />
          <CloudDashboard
            resurrect={data}
            cobolCode={resurrectedSource}
            onStdout={onStdout}
          />
        </div>

        {data?.contract && data.io_protocol && (
          <ExportTab
            cobolCode={resurrectedSource}
            pythonCode={data.verified_python_code}
            schema={data.contract}
            ioProtocol={data.io_protocol}
            initialCode={data.export_fastapi_code}
            programTitle={data.program_title}
          />
        )}

        <ParityBanner report={report} cacheHit={data?.cache_hit ?? false} />
        <AuditTrail attempts={data?.audit_trail} finalReport={report} />
      </div>
    </main>
  );
}
