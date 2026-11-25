import { compoundCurve, splitBar } from "../../../lib/chartGeometry";
import { formatMoneyString } from "../../../lib/money";
import type { MoneyString } from "../../../lib/types";

export interface CompoundChartProps {
  principal: MoneyString;
  accruedInterest: MoneyString;
  maturityAmount: MoneyString;
  annualRatePct: MoneyString;
  compoundsPerYear: MoneyString;
  years: MoneyString;
}

const W = 480n;
const H = 140n;

export function CompoundChart(props: CompoundChartProps) {
  const { principal, accruedInterest, maturityAmount, annualRatePct, compoundsPerYear, years } = props;
  const bar = splitBar(principal, maturityAmount, W);
  const pts = compoundCurve(principal, annualRatePct, compoundsPerYear, years, maturityAmount, W, H);
  const path = pts.map((p) => `${p.x.toString()},${p.y.toString()}`).join(" ");
  const area = pts.length > 0 ? `0,${H.toString()} ${path} ${W.toString()},${H.toString()}` : "";
  return (
    <figure className="rounded-xl border border-slate-200 bg-white p-4">
      <figcaption className="mb-2 text-sm font-semibold text-slate-700">
        Growth to maturity: principal vs accrued interest
      </figcaption>
      <svg
        viewBox={`0 0 ${W.toString()} 250`}
        className="w-full"
        role="img"
        aria-label={`Maturity ${maturityAmount}: principal ${principal}, accrued interest ${accruedInterest}`}
      >
        {pts.length > 0 && (
          <g transform="translate(0 8)">
            <polygon points={area} fill="#D1FAE5" />
            <polyline points={path} fill="none" stroke="#10B981" strokeWidth="2.5" />
          </g>
        )}
        <g transform="translate(0 164)">
          <rect x="0" y="0" width={bar.firstWidth.toString()} height="30" fill="#2563EB" rx="4" />
          <rect x={bar.firstWidth.toString()} y="0" width={bar.secondWidth.toString()} height="30" fill="#10B981" rx="4" />
          <text x="0" y="52" fontSize="13" fill="#1E293B">
            Principal {formatMoneyString(principal)}
          </text>
          <text x={W.toString()} y="52" fontSize="13" fill="#1E293B" textAnchor="end">
            Interest {formatMoneyString(accruedInterest)}
          </text>
        </g>
      </svg>
      <p className="mt-1 text-xs text-slate-500">Maturity {formatMoneyString(maturityAmount)}</p>
    </figure>
  );
}
