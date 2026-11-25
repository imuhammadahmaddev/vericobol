// Chart geometry from Money_String values. BigInt integer arithmetic only.
// Results are bigint pixel values; callers stringify them into SVG attributes.

import type { MoneyString } from "./types";

const DECIMAL_RE = /^(-?)(\d+)(?:\.(\d+))?$/;

export function scaleOfDecimal(raw: MoneyString): number {
  const m = DECIMAL_RE.exec(raw);
  return m ? (m[3] ?? "").length : 0;
}

/** Decimal string to integer units at `scale` (extra fraction digits truncated). Invalid input gives 0n. */
export function toUnits(raw: MoneyString, scale: number): bigint {
  const m = DECIMAL_RE.exec(raw);
  if (!m) return 0n;
  const frac = (m[3] ?? "").padEnd(scale, "0").slice(0, scale);
  const mag = BigInt(m[2] + frac);
  return m[1] === "-" ? -mag : mag;
}

export function commonScale(...values: MoneyString[]): number {
  return values.reduce((acc, v) => Math.max(acc, scaleOfDecimal(v)), 0);
}

/** floor(value * width / total) for non-negative inputs; 0 when total <= 0. Clamped to [0, width]. */
export function proportionalWidth(value: bigint, total: bigint, width: bigint): bigint {
  if (total <= 0n || value <= 0n) return 0n;
  const w = (value * width) / total;
  return w > width ? width : w;
}

export interface SplitBar {
  firstWidth: bigint;
  secondWidth: bigint; // always totalWidth - firstWidth, so the two segments sum exactly
  totalWidth: bigint;
}

/** Split a bar of `totalWidth` into two segments proportional to `first` and `total`. */
export function splitBar(first: MoneyString, total: MoneyString, totalWidth: bigint): SplitBar {
  const scale = commonScale(first, total);
  const firstWidth = proportionalWidth(toUnits(first, scale), toUnits(total, scale), totalWidth);
  return { firstWidth, secondWidth: totalWidth - firstWidth, totalWidth };
}

export interface HBar {
  x: bigint;
  width: bigint;
  negative: boolean;
}

export interface SignedBars {
  zeroX: bigint;
  bars: HBar[];
}

/** Horizontal bars from a shared zero line, supporting negative values. */
export function signedBars(values: MoneyString[], width: bigint): SignedBars {
  const scale = commonScale(...values);
  const units = values.map((v) => toUnits(v, scale));
  let lo = 0n;
  let hi = 0n;
  for (const u of units) {
    if (u < lo) lo = u;
    if (u > hi) hi = u;
  }
  const range = hi - lo;
  const xOf = (u: bigint): bigint => (range === 0n ? 0n : ((u - lo) * width) / range);
  const zeroX = xOf(0n);
  const bars = units.map((u) => {
    const x = xOf(u);
    return u < 0n
      ? { x, width: zeroX - x, negative: true }
      : { x: zeroX, width: x - zeroX, negative: false };
  });
  return { zeroX, bars };
}

export function isFlagSet(flag: MoneyString | undefined): boolean {
  if (flag === undefined) return false;
  return toUnits(flag, scaleOfDecimal(flag)) !== 0n;
}

export interface CurvePoint {
  x: bigint;
  y: bigint; // pixels from the top of the plot area
}

/**
 * Yearly growth curve: P * (1 + r / (100 n)) ^ (n * year), floored in integer units.
 * The final point is pinned to the reported maturity amount so the curve ends on the verified value.
 * Illustration only; the verified numbers are the ones returned by the API.
 */
export function compoundCurve(
  principal: MoneyString,
  annualRatePct: MoneyString,
  compoundsPerYear: MoneyString,
  years: MoneyString,
  maturity: MoneyString,
  width: bigint,
  height: bigint,
): CurvePoint[] {
  const scale = commonScale(principal, maturity);
  const p = toUnits(principal, scale);
  const m = toUnits(maturity, scale);
  const n = toUnits(compoundsPerYear, 0);
  const yrs = toUnits(years, 0);
  if (n <= 0n || yrs <= 0n || m <= 0n) return [];
  const rs = scaleOfDecimal(annualRatePct);
  const den = n * 100n * 10n ** BigInt(rs);
  const num = den + toUnits(annualRatePct, rs);
  const yAt = (v: bigint): bigint => {
    const clamped = v > m ? m : v < 0n ? 0n : v;
    return height - (clamped * height) / m;
  };
  const points: CurvePoint[] = [];
  let numPow = 1n;
  let denPow = 1n;
  for (let year = 0n; year <= yrs; year++) {
    if (year > 0n) {
      for (let i = 0n; i < n; i++) {
        numPow *= num;
        denPow *= den;
      }
    }
    const value = year === yrs ? m : (p * numPow) / denPow;
    points.push({ x: (year * width) / yrs, y: yAt(value) });
  }
  return points;
}

export interface WaterfallStep {
  /** Left edge x in pixels. */
  x: bigint;
  width: bigint;
  /** True when the step lowers the running balance (or a total that is negative). */
  negative: boolean;
}

export interface Waterfall {
  zeroX: bigint;
  steps: WaterfallStep[];
}

/**
 * Floating-bar waterfall. `deltas` are [kind, value]: "total" bars span zero to value,
 * "add" bars raise the running balance by value, "sub" bars lower it by value.
 */
export function waterfallBars(
  items: { kind: "total" | "add" | "sub"; value: MoneyString }[],
  width: bigint,
): Waterfall {
  const scale = commonScale(...items.map((i) => i.value));
  let running = 0n;
  const spans = items.map((it) => {
    const u = toUnits(it.value, scale);
    if (it.kind === "total") {
      running = u;
      return { a: 0n, b: u, negative: u < 0n };
    }
    const start = running;
    running = it.kind === "add" ? running + u : running - u;
    return { a: start, b: running, negative: it.kind === "sub" };
  });
  let lo = 0n;
  let hi = 0n;
  for (const s of spans) {
    for (const v of [s.a, s.b]) {
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
  }
  const range = hi - lo;
  const xOf = (u: bigint): bigint => (range === 0n ? 0n : ((u - lo) * width) / range);
  return {
    zeroX: xOf(0n),
    steps: spans.map((s) => {
      const xa = xOf(s.a);
      const xb = xOf(s.b);
      return { x: xa < xb ? xa : xb, width: xa < xb ? xb - xa : xa - xb, negative: s.negative };
    }),
  };
}
