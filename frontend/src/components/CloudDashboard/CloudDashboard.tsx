"use client";

import { useEffect, useMemo, useState } from "react";
import { useCalculate } from "@/hooks/useCalculate";
import { compareDecimalStrings, formatMoneyString } from "@/lib/money";
import {
  ApiError,
  type DashboardInputMeta,
  type MoneyString,
  type PydanticFieldError,
  type ResurrectResponse,
} from "@/lib/types";
import DynamicChart, { isFlagTruthy } from "./DynamicChart";
import FieldSliderInput from "./FieldSliderInput";

export interface CloudDashboardProps {
  /** Latest resurrect result. Everything on screen is driven by its `dashboard_metadata`. */
  resurrect: ResurrectResponse | null;
  /** The COBOL source that produced `resurrect`. */
  cobolCode: string;
  /** Fired with raw GnuCOBOL stdout after each calculate call (Live 3270 STDOUT). */
  onStdout?: (stdout: string) => void;
  /** Fired whenever the input values change. */
  onInputsChange?: (inputs: Record<string, MoneyString>) => void;
}

function inRange(v: string, f: DashboardInputMeta): boolean {
  try {
    return compareDecimalStrings(v, f.min) >= 0 && compareDecimalStrings(v, f.max) <= 0;
  } catch {
    return false;
  }
}

/** Starting value: field.default if valid and in range, else zero (at the field's scale) if in range, else min. */
function initialValue(f: DashboardInputMeta): MoneyString {
  if (f.default && inRange(f.default, f)) return f.default;
  const frac = (f.step.split(".")[1] ?? "").length;
  const zero = frac > 0 ? `0.${"0".repeat(frac)}` : "0";
  return inRange(zero, f) ? zero : f.min;
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
  if (Array.isArray(d)) {
    return {
      field: null,
      message: null,
      list: (d as PydanticFieldError[]).map((e) => `${e.loc.join(".")}: ${e.msg}`),
    };
  }
  const obj = d as { field?: string; message?: string; error?: string };
  return {
    field: obj.field ?? null,
    message: obj.message ?? obj.error ?? `Request failed (${err.status})`,
    list: [],
  };
}

export default function CloudDashboard({ resurrect, cobolCode, onStdout, onInputsChange }: CloudDashboardProps) {
  const [inputs, setInputs] = useState<Record<string, MoneyString> | null>(null);
  const { trigger, loading, data, error, parityVerified } = useCalculate({ onStdout });

  const meta = resurrect?.dashboard_metadata ?? null;
  const contract = resurrect?.contract ?? null;
  const ioProtocol = resurrect?.io_protocol ?? null;
  const code = resurrect?.verified_python_code ?? null;

  // Reset inputs to each field's minimum when a new program is resurrected.
  useEffect(() => {
    if (!meta) {
      setInputs(null);
      return;
    }
    const init: Record<string, MoneyString> = {};
    for (const f of meta.inputs) init[f.name] = initialValue(f);
    setInputs(init);
  }, [meta]);

  useEffect(() => {
    if (!inputs || !contract || !ioProtocol || !code) return;
    onInputsChange?.(inputs);
    trigger({ cobol_code: cobolCode, python_code: code, schema: contract, io_protocol: ioProtocol }, inputs);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inputs, contract, ioProtocol, code, cobolCode, trigger]);

  const err = useMemo(() => describeError(error), [error]);
  const result = inputs && data ? data.result : null;

  if (!resurrect || !meta) {
    return (
      <section
        aria-label="Resurrected Cloud Dashboard"
        className="rounded-2xl border border-slate-800 bg-[#0F172A] p-5 font-sans text-slate-100"
      >
        <h2 className="mb-2 text-lg font-semibold">2026 Resurrected Cloud Dashboard</h2>
        <p className="text-sm text-slate-400">Resurrect a COBOL program to build its dashboard.</p>
      </section>
    );
  }

  const isStatic = meta.inputs.length === 0;

  return (
    <section
      aria-label="Resurrected Cloud Dashboard"
      className="rounded-2xl border border-slate-800 bg-[#0F172A] p-5 font-sans text-slate-100"
    >
      <div className="mb-4 flex items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">{resurrect.program_title ?? "2026 Resurrected Cloud Dashboard"}</h2>
        {parityVerified !== null && (
          <span
            data-testid="parity-verified"
            className={`rounded-full px-3 py-1 text-xs font-semibold ${
              parityVerified ? "bg-emerald-500/20 text-emerald-300" : "bg-red-500/20 text-red-300"
            }`}
          >
            {parityVerified ? "Parity verified" : "Parity mismatch"}
          </span>
        )}
      </div>

      <div className="space-y-4">
        {isStatic ? (
          <p
            data-testid="static-notice"
            className="rounded-lg border border-slate-700 bg-[#1E293B] p-3 text-sm text-slate-300"
          >
            Static Program — No Runtime Inputs
          </p>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            {meta.inputs.map((f) => (
              <FieldSliderInput
                key={f.name}
                field={f}
                value={inputs?.[f.name] ?? initialValue(f)}
                onChange={(v) => setInputs((prev) => ({ ...(prev ?? {}), [f.name]: v }))}
                serverError={err.field === f.name ? err.message : null}
              />
            ))}
          </div>
        )}

        {err.list.length > 0 && (
          <ul role="alert" className="list-disc rounded-lg bg-red-500/10 p-3 pl-7 text-sm text-red-300">
            {err.list.map((m, idx) => (
              <li key={idx}>{m}</li>
            ))}
          </ul>
        )}
        {err.message && !err.field && (
          <p role="alert" className="rounded-lg bg-red-500/10 p-3 text-sm text-red-300">
            {err.message}
          </p>
        )}

        <div className="grid gap-3 sm:grid-cols-3" aria-busy={loading}>
          {meta.outputs.map((o) => {
            const v = result?.[o.name];
            return (
              <div key={o.name} className="rounded-xl border border-slate-700 bg-[#1E293B] p-4">
                <div className="text-xs uppercase tracking-wide text-slate-400">{o.label}</div>
                <div data-kpi={o.name} className="mt-1 break-all font-mono text-xl font-semibold text-slate-100">
                  {v === undefined
                    ? "…"
                    : o.format === "flag"
                      ? isFlagTruthy(v)
                        ? `SET (${v})`
                        : `CLEAR (${v})`
                      : o.format === "text"
                        ? v
                        : formatMoneyString(v)}
                </div>
              </div>
            );
          })}
        </div>

        {isStatic && data && (
          <pre
            data-testid="static-stdout"
            className="overflow-x-auto rounded-lg border border-slate-700 bg-[#0A0E0C] p-3 font-mono text-xs text-[#00FF66]"
          >
            {data.raw_cobol_output}
          </pre>
        )}

        <DynamicChart
          inputsMeta={meta.inputs}
          outputsMeta={meta.outputs}
          inputs={inputs ?? {}}
          result={result}
        />
      </div>
    </section>
  );
}
