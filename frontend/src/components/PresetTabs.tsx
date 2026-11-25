"use client";

/** One entry of GET /api/presets: raw COBOL text only. */
export interface StarterTemplate {
  id: string;
  title: string;
  cobol_code: string;
}

const LABELS: Record<string, string> = {
  loan_calc: "Loan Amortization",
  compound_int: "Compound Interest",
  daily_ledger: "Daily Ledger",
};

interface PresetTabsProps {
  /** Templates from GET /api/presets; null while loading. */
  templates: StarterTemplate[] | null;
  /** Selected template id, or null for "+ Custom / Blank". */
  selectedId: string | null;
  /** Loads the raw `.cbl` text into the editor. Nothing else is loaded. */
  onSelect: (template: StarterTemplate) => void;
  onCustom: () => void;
  error?: string | null;
  onRetry?: () => void;
  disabled?: boolean;
}

export default function PresetTabs({
  templates,
  selectedId,
  onSelect,
  onCustom,
  error,
  onRetry,
  disabled = false,
}: PresetTabsProps) {
  const pill = (selected: boolean) =>
    `rounded-full border px-3 py-1 text-xs font-medium focus:outline-none focus-visible:ring-2 focus-visible:ring-[#00FF66] disabled:opacity-40 ${
      selected
        ? "border-[#00FF66] bg-[#00FF66]/15 text-[#00FF66]"
        : "border-slate-700 text-slate-400 hover:border-[#00FF66]/60"
    }`;

  return (
    <div
      data-testid="preset-tabs"
      className="flex flex-wrap items-center gap-2"
    >
      <span className="text-xs uppercase tracking-wide text-slate-400">
        Starter Templates:
      </span>
      {(templates ?? []).map((t) => (
        <button
          key={t.id}
          type="button"
          id={`preset-tab-${t.id}`}
          data-testid={`preset-tab-${t.id}`}
          aria-pressed={t.id === selectedId}
          disabled={disabled}
          onClick={() => onSelect(t)}
          className={pill(t.id === selectedId)}
        >
          {LABELS[t.id] ?? t.title}
        </button>
      ))}
      <button
        type="button"
        data-testid="preset-tab-custom"
        aria-pressed={selectedId === null}
        disabled={disabled}
        onClick={onCustom}
        className={pill(selectedId === null)}
      >
        + Custom / Blank
      </button>
      {templates === null && !error && (
        <span data-testid="preset-tabs-loading" className="text-xs text-slate-400">
          Loading templates...
        </span>
      )}
      {error && (
        <span
          role="alert"
          data-testid="preset-tabs-error"
          className="flex items-center gap-2 text-xs text-[#EF4444]"
        >
          Could not load templates: {error}
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="rounded border border-[#EF4444] px-2 py-0.5 hover:bg-[#EF4444] hover:text-white"
            >
              Retry
            </button>
          )}
        </span>
      )}
    </div>
  );
}
