import { splitBar } from "../../../lib/chartGeometry";
import { formatMoneyString } from "../../../lib/money";
import type { MoneyString } from "../../../lib/types";

export interface LoanChartProps {
  principal: MoneyString;
  totalInterest: MoneyString;
  totalPayable: MoneyString;
}

const W = 480n;
const BAR_H = 36;

export function LoanChart({ principal, totalInterest, totalPayable }: LoanChartProps) {
  const { firstWidth, secondWidth } = splitBar(principal, totalPayable, W);
  const fw = firstWidth.toString();
  const sw = secondWidth.toString();
  return (
    <figure className="rounded-xl border border-slate-200 bg-white p-4">
      <figcaption className="mb-2 text-sm font-semibold text-slate-700">
        Total payable: principal vs interest
      </figcaption>
      <svg
        viewBox={`0 0 ${W.toString()} 90`}
        className="w-full"
        role="img"
        aria-label={`Total payable ${totalPayable}: principal ${principal}, interest ${totalInterest}`}
      >
        <rect x="0" y="10" width={fw} height={BAR_H} fill="#2563EB" rx="4" />
        <rect x={fw} y="10" width={sw} height={BAR_H} fill="#F59E0B" rx="4" />
        <text x="0" y="72" fontSize="13" fill="#1E293B">
          Principal {formatMoneyString(principal)}
        </text>
        <text x={W.toString()} y="72" fontSize="13" fill="#1E293B" textAnchor="end">
          Interest {formatMoneyString(totalInterest)}
        </text>
      </svg>
      <p className="mt-1 text-xs text-slate-500">Total payable {formatMoneyString(totalPayable)}</p>
    </figure>
  );
}
