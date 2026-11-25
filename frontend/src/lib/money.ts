// Money_String helpers. All arithmetic uses strings and BigInt only.
// Never convert a money value to a binary floating-point value.

import type { MoneyString } from "./types";

/** Re-exported so existing imports from money.ts keep working. */
export type { MoneyString };

/** The subset of a preset input field needed by these helpers. */
export interface MoneyFieldSchema {
  min: MoneyString;
  max: MoneyString;
  step: MoneyString;
}

export type ValidationResult =
  | { ok: true; value: MoneyString }
  | { ok: false; message: string };

const DECIMAL_RE = /^(-?)(\d+)(?:\.(\d+))?$/;

interface ParsedDecimal {
  negative: boolean;
  intPart: string;
  fracPart: string;
}

function parseDecimal(raw: string): ParsedDecimal {
  const m = DECIMAL_RE.exec(raw);
  if (!m) {
    throw new RangeError(`Invalid decimal string: ${JSON.stringify(raw)}`);
  }
  return { negative: m[1] === "-", intPart: m[2], fracPart: m[3] ?? "" };
}

function scaleOf(raw: string): number {
  return parseDecimal(raw).fracPart.length;
}

/** Convert a decimal string to integer units at `scale`. Extra fraction digits are truncated. */
function toUnits(raw: string, scale: number): bigint {
  const p = parseDecimal(raw);
  const frac = p.fracPart.padEnd(scale, "0").slice(0, scale);
  const magnitude = BigInt(p.intPart + frac);
  return p.negative ? -magnitude : magnitude;
}

/** Format integer units at `scale` as a decimal string. */
function fromUnits(units: bigint, scale: number): MoneyString {
  const negative = units < 0n;
  const digits = (negative ? -units : units).toString().padStart(scale + 1, "0");
  const intPart = digits.slice(0, digits.length - scale);
  const fracPart = digits.slice(digits.length - scale);
  const body = scale > 0 ? `${intPart}.${fracPart}` : intPart;
  return negative ? `-${body}` : body;
}

function maxScale(...values: string[]): number {
  return values.reduce((acc, v) => Math.max(acc, scaleOf(v)), 0);
}

/** `min_units + index * step_units`, formatted with the decimal point at `scale`. */
export function stepIndexToMoneyString(
  index: bigint,
  min: MoneyString,
  step: MoneyString,
  scale: number,
): MoneyString {
  return fromUnits(toUnits(min, scale) + index * toUnits(step, scale), scale);
}

/** Inverse of `stepIndexToMoneyString`: `(value - min) / step` in integer units (floor). */
export function moneyStringToStepIndex(
  value: MoneyString,
  min: MoneyString,
  step: MoneyString,
  scale: number,
): bigint {
  const stepUnits = toUnits(step, scale);
  if (stepUnits === 0n) return 0n;
  const diff = toUnits(value, scale) - toUnits(min, scale);
  let q = diff / stepUnits; // BigInt division truncates toward zero
  if (diff % stepUnits !== 0n && (diff < 0n) !== (stepUnits < 0n)) q -= 1n;
  return q;
}

/**
 * Clamp `value` into [min, max] and round to the nearest step multiple above min
 * (half rounds up). Invalid input falls back to `min`.
 */
export function clampToSchema(value: MoneyString, field: MoneyFieldSchema): MoneyString {
  const scale = maxScale(field.min, field.max, field.step);
  const minU = toUnits(field.min, scale);
  const maxU = toUnits(field.max, scale);
  const stepU = toUnits(field.step, scale);

  if (!DECIMAL_RE.test(value)) return fromUnits(minU, scale);

  const vScale = Math.max(scale, scaleOf(value));
  // Work at a scale that can hold the value exactly, then round to the field grid.
  const minV = toUnits(field.min, vScale);
  const maxV = toUnits(field.max, vScale);
  let v = toUnits(value, vScale);
  if (v < minV) v = minV;
  if (v > maxV) v = maxV;

  // Degenerate schema (non-positive step or min > max): fall back to min.
  if (stepU <= 0n || maxU < minU) return fromUnits(minU, scale);

  const stepV = toUnits(field.step, vScale);
  const diff = v - minV;
  let idx = (2n * diff + stepV) / (2n * stepV); // round half up, diff >= 0
  const maxIdx = (maxV - minV) / stepV;
  if (idx > maxIdx) idx = maxIdx;
  return stepIndexToMoneyString(idx, field.min, field.step, scale);
}

/**
 * Parse with a strict decimal regex, require fraction digits <= those of `step`,
 * and require min <= value <= max.
 */
export function validateDecimal(raw: string, field: MoneyFieldSchema): ValidationResult {
  if (!DECIMAL_RE.test(raw)) {
    return { ok: false, message: "Enter a valid decimal number" };
  }
  const allowedScale = scaleOf(field.step);
  if (scaleOf(raw) > allowedScale) {
    return {
      ok: false,
      message:
        allowedScale === 0
          ? "Enter a whole number"
          : `At most ${allowedScale} decimal place${allowedScale === 1 ? "" : "s"}`,
    };
  }
  if (compareDecimalStrings(raw, field.min) < 0) {
    return { ok: false, message: `Must be at least ${field.min}` };
  }
  if (compareDecimalStrings(raw, field.max) > 0) {
    return { ok: false, message: `Must be at most ${field.max}` };
  }
  return { ok: true, value: raw };
}

/** Compare two decimal strings exactly. Throws RangeError on malformed input. */
export function compareDecimalStrings(a: MoneyString, b: MoneyString): -1 | 0 | 1 {
  const scale = Math.max(scaleOf(a), scaleOf(b));
  const ua = toUnits(a, scale);
  const ub = toUnits(b, scale);
  return ua < ub ? -1 : ua > ub ? 1 : 0;
}

/** Insert thousands separators into the integer part by string operations. Non-decimal input is returned unchanged. */
export function formatMoneyString(value: MoneyString): string {
  const m = DECIMAL_RE.exec(value);
  if (!m) return value;
  const [, sign, intPart, frac] = m;
  let grouped = "";
  for (let i = 0; i < intPart.length; i++) {
    if (i > 0 && (intPart.length - i) % 3 === 0) grouped += ",";
    grouped += intPart[i];
  }
  return `${sign}${grouped}${frac !== undefined ? `.${frac}` : ""}`;
}
