"use client";

// Native SVG horizontal bar chart. Geometry is BigInt-only (lib/chartGeometry).
import { signedBars } from "@/lib/chartGeometry";
import { formatMoneyString } from "@/lib/money";
import type { DashboardInputMeta, DashboardOutputMeta, MoneyString } from "@/lib/types";

export interface DynamicChartProps {
  inputsMeta: DashboardInputMeta[];
  outputsMeta: DashboardOutputMeta[];
  /** Current input values (decimal strings). */
  inputs: Record<string, MoneyString>;
  /** Latest outputs from /api/calculate-custom (decimal strings). */
  result: Record<string, MoneyString> | null;
}

const DECIMAL_RE = /^-?\d+(?:\.\d+)?$/;
const CHART_WIDTH = 480n;
const LABEL_PAD = 8;
const ROW_H = 44;
const BAR_H = 16;

interface Row {
  key: string;
  label: string;
  value: MoneyString;
  kind: "input" | "output";
}

export function isFlagTruthy(value: MoneyString | undefined): boolean {
  if (value === undefined) return false;
  const v = value.trim().toUpperCase();
  if (v === "Y" || v === "YES" || v === "TRUE" || v === "OVERDRAWN") return true;
  return DECIMAL_RE.test(v) && /[1-9]/.test(v);
}

export default function DynamicChart({ inputsMeta, outputsMeta, inputs, result }: DynamicChartProps) {
  const rows: Row[] = [];
  for (const f of inputsMeta) {
    const v = inputs[f.name];
    if (v !== undefined && DECIMAL_RE.test(v)) rows.push({ key: `in-${f.name}`, label: f.label, value: v, kind: "input" });
  }
  const flags: { name: string; label: string; value: MoneyString }[] = [];
  if (result) {
    for (const o of outputsMeta) {
      const v = result[o.name];
      if (v === undefined) continue;
      if (o.format === "flag") flags.push({ name: o.name, label: o.label, value: v });
      else if (o.format !== "text" && DECIMAL_RE.test(v)) {
        rows.push({ key: `out-${o.name}`, label: o.label, value: v, kind: "output" });
      }
    }
  }

  if (rows.length === 0 && flags.length === 0) return null;

  const { zeroX, bars } = signedBars(rows.map((r) => r.value), CHART_WIDTH);
  const height = rows.length * ROW_H + 8;
  const viewW = CHART_WIDTH.toString();

  return (
    <div
      className="rounded-xl border border-slate-700 bg-[#1E293B] p-4"
      data-testid="dynamic-chart"
    >
      <h3 className="mb-2 text-sm font-semibold text-slate-100">Inputs vs. Verified Outputs</h3>

      {rows.length > 0 && (
        <svg
          role="img"
          aria-label="Horizontal bar comparison of inputs and outputs"
          viewBox={`0 0 ${viewW} ${height}`}
          className="w-full"
          preserveAspectRatio="xMinYMin meet"
        >
          {rows.map((r, idx) => {
            const bar = bars[idx];
            const y = idx * ROW_H + 4;
            const fill = bar.negative ? "#EF4444" : r.kind === "output" ? "#10B981" : "#38BDF8";
            return (
              <g key={r.key} data-bar={r.key}>
                <text x={LABEL_PAD} y={y + 10} fontSize="11" fill="#94A3B8">
                  {r.label}: {formatMoneyString(r.value)}
                </text>
                <rect
                  x={bar.x.toString()}
                  y={y + 16}
                  width={bar.width.toString()}
                  height={BAR_H}
                  rx="3"
                  fill={fill}
                />
              </g>
            );
          })}
          <line
            data-testid="zero-line"
            x1={zeroX.toString()}
            x2={zeroX.toString()}
            y1="0"
            y2={height}
            stroke="#E2E8F0"
            strokeWidth="1"
            strokeDasharray="3 3"
          />
        </svg>
      )}

      {flags.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {flags.map((f) => {
            const on = isFlagTruthy(f.value);
            return (
              <span
                key={f.name}
                data-flag={f.name}
                className={`rounded-full px-3 py-1 text-xs font-semibold ${
                  on ? "bg-red-500/20 text-red-300" : "bg-emerald-500/20 text-emerald-300"
                }`}
              >
                {f.label}: {on ? "SET" : "CLEAR"} ({f.value})
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}
