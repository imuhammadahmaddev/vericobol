"use client";

import { useState } from "react";
import type { PicMapping } from "@/lib/types";
import BusinessRulesAndPicMap from "../BusinessRulesAndPicMap";
import RejectionDetail from "../RejectionDetail";
import CobolSourceEditor, {
  MAINFRAME_FONT,
  PHOSPHOR_GLOW,
} from "./CobolSourceEditor";
import RawStdoutView from "./RawStdoutView";

type TabId = "source" | "stdout" | "rules";

const TABS: { id: TabId; label: string }[] = [
  { id: "source", label: "COBOL Source" },
  { id: "stdout", label: "Live 3270 STDOUT" },
  { id: "rules", label: "Extracted Rules & PIC Map" },
];

interface LegacyPanelProps {
  source: string;
  onSourceChange: (source: string) => void;
  edited?: boolean;
  onLoadSandboxSnippet?: () => void;
  /** `raw_cobol_output` from the latest /api/calculate response. */
  liveOutput?: string | null;
  /** `raw_cobol_sample_output` from the verification report. */
  batchSample?: string[] | null;
  businessRules: string[];
  picMappings: PicMapping[];
  /** Sandbox rejection or other error from the last resurrect call. */
  error?: unknown;
  disabled?: boolean;
}

export default function LegacyPanel({
  source,
  onSourceChange,
  edited = false,
  onLoadSandboxSnippet,
  liveOutput,
  batchSample,
  businessRules,
  picMappings,
  error,
  disabled = false,
}: LegacyPanelProps) {
  const [tab, setTab] = useState<TabId>("source");
  const [editing, setEditing] = useState(false);

  const onKeyDown = (e: React.KeyboardEvent, index: number) => {
    let next = index;
    if (e.key === "ArrowRight") next = (index + 1) % TABS.length;
    else if (e.key === "ArrowLeft") next = (index - 1 + TABS.length) % TABS.length;
    else return;
    e.preventDefault();
    setTab(TABS[next].id);
    document.getElementById(`legacy-tab-${TABS[next].id}`)?.focus();
  };

  return (
    <section
      aria-label="1959 IBM 3270 Mainframe"
      data-testid="legacy-panel"
      className="relative overflow-hidden rounded border-2 border-[#00FF66]/60 bg-[#0A0E0C] text-[#00FF66] shadow-[0_0_16px_rgba(0,255,102,0.25)]"
      style={{ fontFamily: MAINFRAME_FONT, textShadow: PHOSPHOR_GLOW }}
    >
      <h2 className="border-b border-[#00FF66]/40 px-3 py-2 text-sm uppercase tracking-widest">
        1959 IBM 3270 Mainframe
      </h2>

      <div
        role="tablist"
        aria-label="Legacy mainframe views"
        className="flex flex-wrap border-b border-[#00FF66]/30"
      >
        {TABS.map((t, i) => {
          const selected = tab === t.id;
          return (
            <button
              key={t.id}
              type="button"
              role="tab"
              id={`legacy-tab-${t.id}`}
              data-testid={`legacy-tab-${t.id}`}
              aria-selected={selected}
              aria-controls={`legacy-panel-${t.id}`}
              tabIndex={selected ? 0 : -1}
              onClick={() => setTab(t.id)}
              onKeyDown={(e) => onKeyDown(e, i)}
              className={`px-3 py-2 text-xs uppercase tracking-wide focus:outline-none focus:ring-1 focus:ring-inset focus:ring-[#00FF66] ${
                selected
                  ? "bg-[#00FF66]/15 text-[#00FF66]"
                  : "text-[#00FF66]/60 hover:bg-[#00FF66]/10"
              }`}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      {error != null && (
        <div className="p-2">
          <RejectionDetail error={error} />
        </div>
      )}

      <div
        role="tabpanel"
        id={`legacy-panel-${tab}`}
        aria-labelledby={`legacy-tab-${tab}`}
      >
        {tab === "source" && (
          <CobolSourceEditor
            source={source}
            onSourceChange={onSourceChange}
            edited={edited}
            editing={editing}
            onEditingChange={setEditing}
            onLoadSandboxSnippet={onLoadSandboxSnippet}
            disabled={disabled}
          />
        )}
        {tab === "stdout" && (
          <RawStdoutView liveOutput={liveOutput} batchSample={batchSample} />
        )}
        {tab === "rules" && (
          <BusinessRulesAndPicMap
            businessRules={businessRules}
            picMappings={picMappings}
          />
        )}
      </div>
    </section>
  );
}
