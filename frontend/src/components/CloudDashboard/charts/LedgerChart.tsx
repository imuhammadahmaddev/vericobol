import { isFlagSet, waterfallBars } from "../../../lib/chartGeometry";
import { formatMoneyString } from "../../../lib/money";
import type { MoneyString } from "../../../lib/types";

export interface LedgerChartProps {
  openingBalance: MoneyString;
  deposits: MoneyString;
  withdrawals: MoneyString;
  feeApplied: MoneyString;
  closingBalance: MoneyString;
  overdraftFlag: MoneyString;
}

const W = 380n;
const LABEL_W = 110n;
const ROW = 44;

export function LedgerChart({
  openingBalance,
  deposits,
  withdrawals,
  feeApplied,
  closingBalance,
  overdraftFlag,
}: LedgerChartProps) {
  const triggered = isFlagSet(overdraftFlag);
  const rows = [
    { label: "Opening", sign: "", kind: "total" as const, value: openingBalance, color: "#2563EB" },
    { label: "+ Deposits", sign: "+", kind: "add" as const, value: deposits, color: "#10B981" },
    { label: "- Withdrawals", sign: "-", kind: "sub" as const, value: withdrawals, color: "#F59E0B" },
    { label: "Overdraft Fee", sign: "-", kind: "sub" as const, value: feeApplied, color: "#EF4444" },
    { label: "Closing", sign: "", kind: "total" as const, value: closingBalance, color: triggered ? "#EF4444" : "#0F766E" },
  ];
  const { zeroX, steps } = waterfallBars(rows.map((r) => ({ kind: r.kind, value: r.value })), W);
  const total = (LABEL_W + W + 10n).toString();
  const height = rows.length * ROW + 10;
  return (
    <figure className="rounded-xl border border-slate-200 bg-white p-4">
      <figcaption className="mb-2 flex items-center justify-between text-sm font-semibold text-slate-700">
        <span>Daily ledger waterfall</span>
        {triggered ? (
          <span className="rounded-full bg-red-100 px-3 py-1 text-xs font-semibold text-red-700">
            Overdraft Triggered
          </span>
        ) : (
          <span className="rounded-full bg-emerald-100 px-3 py-1 text-xs font-semibold text-emerald-700">
            Account Healthy
          </span>
        )}
      </figcaption>
      <svg
        viewBox={`0 0 ${total} ${height}`}
        className="w-full"
        role="img"
        aria-label={`Opening ${openingBalance}, deposits ${deposits}, withdrawals ${withdrawals}, overdraft fee ${feeApplied}, closing ${closingBalance}`}
      >
        <line
          x1={(LABEL_W + zeroX).toString()}
          x2={(LABEL_W + zeroX).toString()}
          y1="0"
          y2={height}
          stroke="#94A3B8"
          strokeDasharray="4 3"
        />
        {rows.map((r, i) => {
          const s = steps[i];
          const y = 6 + i * ROW;
          return (
            <g key={r.label}>
              <text x="0" y={y + 17} fontSize="12" fill="#334155">
                {r.label}
              </text>
              <rect
                x={(LABEL_W + s.x).toString()}
                y={y}
                width={s.width.toString()}
                height="24"
                fill={r.color}
                rx="3"
              />
              <text x={(LABEL_W + 4n).toString()} y={y + 37} fontSize="11" fill="#475569">
                {r.sign}
                {formatMoneyString(r.value)}
              </text>
            </g>
          );
        })}
      </svg>
    </figure>
  );
}
