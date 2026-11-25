"use client";

import { useEffect, useId, useState } from "react";
import {
  compareDecimalStrings,
  moneyStringToStepIndex,
  stepIndexToMoneyString,
  validateDecimal,
} from "@/lib/money";
import type { MoneyString, PresetInputField } from "@/lib/types";

export interface FieldSliderInputProps {
  field: PresetInputField;
  /** Current committed value (always a valid decimal string). */
  value: MoneyString;
  onChange: (value: MoneyString) => void;
  /** Per-field server error (e.g. from a 422 response). */
  serverError?: string | null;
  disabled?: boolean;
}

function fracLen(s: string): number {
  const i = s.indexOf(".");
  return i < 0 ? 0 : s.length - i - 1;
}

/** Slider (integer step index) plus exact decimal text input. Strings and BigInt only. */
export default function FieldSliderInput({
  field,
  value,
  onChange,
  serverError,
  disabled,
}: FieldSliderInputProps) {
  const id = useId();
  const errId = `${id}-err`;
  const [draft, setDraft] = useState<string>(value);
  const [localError, setLocalError] = useState<string | null>(null);

  // Re-sync the draft when the committed value changes from outside.
  useEffect(() => {
    setDraft((d) => {
      try {
        return compareDecimalStrings(d, value) === 0 ? d : value;
      } catch {
        return value;
      }
    });
    setLocalError(null);
  }, [value]);

  const scale = Math.max(fracLen(field.min), fracLen(field.max), fracLen(field.step));

  let maxIndex = 0;
  let curIndex = 0;
  try {
    maxIndex = parseInt(
      moneyStringToStepIndex(field.max, field.min, field.step, scale).toString(),
      10,
    );
    const idx = moneyStringToStepIndex(value, field.min, field.step, scale);
    curIndex = Math.min(Math.max(parseInt(idx.toString(), 10), 0), maxIndex);
  } catch {
    // Malformed schema or value: leave the slider at 0.
  }

  const handleSlider = (raw: string) => {
    try {
      const next = stepIndexToMoneyString(BigInt(raw), field.min, field.step, scale);
      setDraft(next);
      setLocalError(null);
      onChange(next);
    } catch {
      /* ignore malformed slider input */
    }
  };

  const handleText = (raw: string) => {
    setDraft(raw);
    const res = validateDecimal(raw, field);
    if (res.ok) {
      setLocalError(null);
      onChange(res.value);
    } else {
      setLocalError(res.message);
    }
  };

  const message = localError ?? serverError ?? null;
  const invalid = message !== null;

  return (
    <div className="rounded-xl border border-slate-700 bg-[#1E293B] p-4">
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <label htmlFor={`${id}-text`} className="text-sm font-medium text-slate-200">
          {field.label}
        </label>
        <span className="text-xs text-slate-400">
          {field.min} – {field.max}
        </span>
      </div>

      <input
        type="range"
        aria-label={`${field.label} slider`}
        min={0}
        max={maxIndex}
        step={1}
        value={curIndex}
        disabled={disabled}
        onChange={(e) => handleSlider(e.target.value)}
        className="mb-3 w-full accent-emerald-500"
      />

      <input
        id={`${id}-text`}
        name={field.name}
        type="text"
        inputMode="decimal"
        autoComplete="off"
        value={draft}
        disabled={disabled}
        onChange={(e) => handleText(e.target.value)}
        aria-invalid={invalid}
        aria-describedby={invalid ? errId : undefined}
        className={`w-full rounded-lg border bg-[#0F172A] px-3 py-2 font-mono text-sm text-slate-100 outline-none focus:ring-2 ${
          invalid
            ? "border-red-500 focus:ring-red-500"
            : "border-slate-600 focus:ring-emerald-500"
        }`}
      />

      <p
        id={errId}
        role={invalid ? "alert" : undefined}
        data-field-error={field.name}
        className="mt-1 min-h-[1rem] text-xs text-red-400"
      >
        {message}
      </p>
    </div>
  );
}
