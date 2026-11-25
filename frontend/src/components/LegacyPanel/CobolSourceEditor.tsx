"use client";

import { useRef } from "react";
import { SANDBOX_TEST_SNIPPET } from "@/lib/sandboxSnippet";

export const MAINFRAME_FONT =
  'var(--font-jetbrains-mono), "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';

export const PHOSPHOR_GLOW = "0 0 6px rgba(0,255,102,0.55)";

interface CobolSourceEditorProps {
  /** Current COBOL source (preset source or the user's edits). */
  source: string;
  /** Called with the edited source; the parent sends it as `cobol_code` on the next Resurrect. */
  onSourceChange: (source: string) => void;
  /** True when the source differs from the selected preset's original. */
  edited?: boolean;
  /** Edit mode (textarea) vs read-only line-numbered viewer. */
  editing: boolean;
  onEditingChange: (editing: boolean) => void;
  /** Optional hook, e.g. so the parent can drop the preset field_schema. */
  onLoadSandboxSnippet?: () => void;
  disabled?: boolean;
}

export default function CobolSourceEditor({
  source,
  onSourceChange,
  edited = false,
  editing,
  onEditingChange,
  onLoadSandboxSnippet,
  disabled = false,
}: CobolSourceEditorProps) {
  const gutterRef = useRef<HTMLDivElement>(null);
  const lines = source.split("\n");
  const lineNumbers = lines.map((_, i) => i + 1).join("\n");

  const loadSnippet = () => {
    onSourceChange(SANDBOX_TEST_SNIPPET);
    onLoadSandboxSnippet?.();
    onEditingChange(true);
  };

  const btn =
    "rounded border border-[#00FF66]/60 px-2 py-1 text-xs uppercase tracking-wide text-[#00FF66] hover:bg-[#00FF66]/10 focus:outline-none focus:ring-1 focus:ring-[#00FF66] disabled:opacity-40";

  return (
    <section
      aria-label="COBOL source"
      data-testid="cobol-source-editor"
      className="bg-[#0A0E0C] text-[#00FF66]"
      style={{ fontFamily: MAINFRAME_FONT, textShadow: PHOSPHOR_GLOW }}
    >
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#00FF66]/30 px-3 py-2">
        <span className="text-xs uppercase tracking-widest">
          COBOL SOURCE{edited ? " *MODIFIED*" : ""}
        </span>
        <div className="flex gap-2">
          <button
            type="button"
            className={btn}
            data-testid="toggle-edit"
            aria-pressed={editing}
            disabled={disabled}
            onClick={() => onEditingChange(!editing)}
          >
            {editing ? "View" : "Edit"}
          </button>
          <button
            type="button"
            className={btn}
            data-testid="load-sandbox-snippet"
            disabled={disabled}
            onClick={loadSnippet}
          >
            Test Sandbox (CALL SYSTEM)
          </button>
        </div>
      </div>

      <div className="relative flex max-h-[28rem] overflow-hidden text-sm leading-5">
        <div
          ref={gutterRef}
          aria-hidden="true"
          className="select-none overflow-hidden whitespace-pre border-r border-[#00FF66]/30 px-2 py-2 text-right text-[#00FF66]/50"
        >
          {lineNumbers}
        </div>
        {editing ? (
          <textarea
            data-testid="cobol-textarea"
            aria-label="Edit COBOL source"
            spellCheck={false}
            wrap="off"
            value={source}
            disabled={disabled}
            onChange={(e) => onSourceChange(e.target.value)}
            onScroll={(e) => {
              if (gutterRef.current)
                gutterRef.current.scrollTop = e.currentTarget.scrollTop;
            }}
            className="min-h-[12rem] w-full resize-none overflow-auto whitespace-pre bg-transparent px-3 py-2 leading-5 text-[#00FF66] caret-[#00FF66] outline-none"
            style={{ fontFamily: MAINFRAME_FONT, textShadow: PHOSPHOR_GLOW }}
          />
        ) : (
          <pre
            data-testid="cobol-viewer"
            tabIndex={0}
            aria-label="COBOL source (read only)"
            className="w-full overflow-auto px-3 py-2 leading-5 outline-none focus:ring-1 focus:ring-[#00FF66]/50"
          >
            {source}
          </pre>
        )}
      </div>
    </section>
  );
}
