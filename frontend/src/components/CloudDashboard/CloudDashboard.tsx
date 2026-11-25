"use client";

import { useEffect, useMemo, useState } from "react";
import { useCalculate } from "@/hooks/useCalculate";
import { formatMoneyString } from "@/lib/money";
import {
  ApiError,
  type CalculateResponse,
  type MoneyString,
  type PresetSummary,
  type PydanticFieldError,
  type ResurrectResponse,
} from "@/lib/types";
import VerifiedPythonTab from "../VerifiedPythonTab";
import { CompoundChart } from "./charts/CompoundChart";
import { LedgerChart } from "./charts/LedgerChart";
import { LoanChart } from "./charts/LoanChart";
import ExportTab from "./ExportTab";
import FieldSliderInput from "./FieldSliderInput";

export interface CloudDashboardProps {
  preset: PresetSummary | null;
  /** Latest resurrect result; feeds the Verified Python tab. */
  resurrect: ResurrectResponse | null;
  /** Fired whenever a fresh calculate response arrives (for the left STDOUT pane). */
  onCalculated?: (result: CalculateResponse, rawCobolOutput: string) => void;
  /** Fired whenever the input values change (including the initial sample). */
  onInputsChange?: (inputs: Record<string, MoneyString>) => void;
}

type TabId = "dashboard" | "python" | "export";

const TABS: { id: TabId; label: string }[] = [
  { id: "dashboard", label: "Interactive Financial Dashboard" },
  { id: "python", label: "Verified Python Code" },
  { id: "export", label: "Export FastAPI Microservice" },
];

const KPIS: Record<string, { key: string; label: string }[]> = {
  loan_calc: [
    { key: "monthly_payment", label: "Monthly Payment" },
    { key: "total_interest", label: "Total Interest" },
    { key: "total_payable", label: "Total Payable" },
  ],
  compound_int: [
    { key: "maturity_amount", label: "Maturity Amount" },
    { key: "accrued_interest", label: "Accrued Interest" },
  ],
  daily_ledger: [
    { key: "closing_balance", label: "Closing Balance" },
    { key: "fee_applied", label: "Fee Applied" },
    { key: "overdraft_flag", label: "Overdraft Flag" },
  ],
};

function isPydanticArray(d: unknown): d is PydanticFieldError[] {
  return Array.isArray(d);
}

function describeError(err: Error | null): {
  field: string | null;
  message: string | null;
  list: string[];
} {
  if (!err) return { field: null, message: null, list: [] };
  if (!(err instanceof ApiError)) return { field: null, message: err.message, list: [] };
  const d = err.detail;
  if (typeof d === "string") return { field: null, message: d, list: [] };
  if (isPydanticArray(d)) {
    return { field: null, message: null, list: d.map((e) => `${e.loc.join(".")}: ${e.msg}`) };
  }
  const obj = d as { field?: string; message?: string; error?: string };
  return {
    field: obj.field ?? null,
    message: obj.message ?? obj.error ?? `Request failed (${err.status})`,
    list: [],
  };
}

function Results({ presetId, data, inputs }: { presetId: string; data: CalculateResponse; inputs: Record<string, MoneyString> }) {
  const r = data.result;
  const g = (k: string): MoneyString => r[k] ?? "0";
  const i = (k: string): MoneyString => inputs[k] ?? "0";
  if (presetId === "loan_calc") {
    return (
      <LoanChart principal={i("principal")} totalInterest={g("total_interest")} totalPayable={g("total_payable")} />
    );
  }
  if (presetId === "compound_int") {
    return (
      <CompoundChart
        principal={i("principal")}
        accruedInterest={g("accrued_interest")}
        maturityAmount={g("maturity_amount")}
        annualRatePct={i("annual_rate_pct")}
        compoundsPerYear={i("compounds_per_year")}
        years={i("years")}
      />
    );
  }
  if (presetId === "daily_ledger") {
    return (
      <LedgerChart
        openingBalance={i("opening_balance")}
        deposits={i("deposits")}
        withdrawals={i("withdrawals")}
        feeApplied={g("fee_applied")}
        closingBalance={g("closing_balance")}
        overdraftFlag={g("overdraft_flag")}
      />
    );
  }
  return null;
}

export default function CloudDashboard({ preset, resurrect, onCalculated, onInputsChange }: CloudDashboardProps) {
  const [tab, setTab] = useState<TabId>("dashboard");
  const [inputs, setInputs] = useState<Record<string, MoneyString>>({});
  const { trigger, loading, data, error } = useCalculate();

  const presetId = preset?.preset_id ?? null;

  // Reset inputs to the preset's sample when the preset changes.
  useEffect(() => {
    if (preset) setInputs({ ...preset.sample_input });
  }, [preset]);

  useEffect(() => {
    if (!presetId || Object.keys(inputs).length === 0) return;
    onInputsChange?.(inputs);
    trigger(presetId, inputs);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [presetId, inputs, trigger]);

  useEffect(() => {
    if (data) onCalculated?.(data, data.raw_cobol_output);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  const err = useMemo(() => describeError(error), [error]);
  const fresh = data && presetId && data.preset_id === presetId ? data : null;

  return (
    <section
      aria-label="2026 Resurrected Cloud Dashboard"
      className="rounded-2xl border border-slate-200 bg-slate-50 p-5 font-sans text-slate-900"
    >
      <h2 className="mb-4 text-lg font-semibold">2026 Resurrected Cloud Dashboard</h2>

      <div role="tablist" className="mb-4 flex flex-wrap gap-2">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            type="button"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className={`rounded-lg px-3 py-2 text-sm font-medium focus:outline-none focus:ring-2 focus:ring-emerald-500 ${
              tab === t.id ? "bg-emerald-600 text-white" : "bg-white text-slate-700 border border-slate-300"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {!preset && <p className="text-sm text-slate-500">Select a preset to begin.</p>}

      {preset && tab === "dashboard" && (
        <div role="tabpanel" className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            {preset.input_schema.map((f) => (
              <FieldSliderInput
                key={`${preset.preset_id}-${f.name}`}
                field={f}
                value={inputs[f.name] ?? preset.sample_input[f.name] ?? f.min}
                onChange={(v) => setInputs((prev) => ({ ...prev, [f.name]: v }))}
                serverError={err.field === f.name ? err.message : null}
              />
            ))}
          </div>

          {err.list.length > 0 && (
            <ul role="alert" className="list-disc rounded-lg bg-red-50 p-3 pl-7 text-sm text-red-700">
              {err.list.map((m, idx) => (
                <li key={idx}>{m}</li>
              ))}
            </ul>
          )}
          {err.message && !err.field && (
            <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-700">
              {err.message}
            </p>
          )}

          <div className="grid gap-3 sm:grid-cols-3" aria-busy={loading}>
            {(KPIS[preset.preset_id] ?? []).map((k) => (
              <div key={k.key} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                <div className="text-xs uppercase tracking-wide text-slate-500">{k.label}</div>
                <div data-kpi={k.key} className="mt-1 font-mono text-xl font-semibold text-slate-900">
                  {fresh?.result[k.key] !== undefined
                    ? k.key === "overdraft_flag"
                      ? fresh.result[k.key]
                      : formatMoneyString(fresh.result[k.key])
                    : "…"}
                </div>
              </div>
            ))}
          </div>

          {fresh && <Results presetId={preset.preset_id} data={fresh} inputs={inputs} />}
        </div>
      )}

      {preset && tab === "python" && (
        <div role="tabpanel">
          {resurrect ? (
            <VerifiedPythonTab result={resurrect} />
          ) : (
            <p className="text-sm text-slate-500">Run Resurrect to generate verified Python.</p>
          )}
        </div>
      )}

      {preset && tab === "export" && (
        <div role="tabpanel">
          <ExportTab presetId={preset.preset_id} />
        </div>
      )}
    </section>
  );
}
