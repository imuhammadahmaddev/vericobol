"use client";

import type { KeyboardEvent } from "react";
import type { PresetId, PresetSummary } from "@/lib/types";

interface PresetTabsProps {
  /** Presets from GET /api/presets; null while loading. */
  presets: PresetSummary[] | null;
  selectedId: PresetId | null;
  onSelect: (presetId: PresetId) => void;
  /** Set when GET /api/presets failed. */
  error?: string | null;
  onRetry: () => void;
  loading?: boolean;
}

export default function PresetTabs({
  presets,
  selectedId,
  onSelect,
  error,
  onRetry,
  loading = false,
}: PresetTabsProps) {
  if (error) {
    return (
      <div
        role="alert"
        data-testid="preset-tabs-error"
        className="flex items-center justify-between gap-3 rounded-lg border border-bug px-3 py-2 text-sm text-bug"
      >
        <span>
          Could not load presets from GET /api/presets: {error}
        </span>
        <button
          type="button"
          onClick={onRetry}
          className="rounded border border-bug px-3 py-1 font-medium hover:bg-bug hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-bug"
        >
          Retry
        </button>
      </div>
    );
  }

  if (!presets) {
    return (
      <div
        data-testid="preset-tabs-loading"
        className="text-sm text-gray-400"
        aria-busy={loading}
      >
        Loading presets...
      </div>
    );
  }

  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (presets.length === 0) return;
    let next = index;
    if (e.key === "ArrowRight") next = (index + 1) % presets.length;
    else if (e.key === "ArrowLeft")
      next = (index - 1 + presets.length) % presets.length;
    else return;
    e.preventDefault();
    onSelect(presets[next].preset_id);
    document.getElementById(`preset-tab-${presets[next].preset_id}`)?.focus();
  };

  return (
    <div
      role="tablist"
      aria-label="Preset COBOL programs"
      data-testid="preset-tabs"
      className="flex flex-wrap gap-2"
    >
      {presets.map((p, i) => {
        const selected = p.preset_id === selectedId;
        return (
          <button
            key={p.preset_id}
            id={`preset-tab-${p.preset_id}`}
            type="button"
            role="tab"
            aria-selected={selected}
            tabIndex={selected ? 0 : -1}
            title={p.description}
            data-testid={`preset-tab-${p.preset_id}`}
            onClick={() => onSelect(p.preset_id)}
            onKeyDown={(e) => onKeyDown(e, i)}
            className={`rounded-md border px-3 py-1.5 text-sm font-medium focus:outline-none focus-visible:ring-2 focus-visible:ring-verified ${
              selected
                ? "border-verified bg-verified text-white"
                : "border-gray-500 text-gray-300 hover:border-verified"
            }`}
          >
            {p.name}
          </button>
        );
      })}
    </div>
  );
}
