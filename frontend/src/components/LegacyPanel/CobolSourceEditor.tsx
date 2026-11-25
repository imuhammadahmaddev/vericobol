"use client";

import { useRef, useState } from "react";
import type { DragEvent } from "react";
import { SANDBOX_TEST_SNIPPET } from "@/lib/sandboxSnippet";

export const MAINFRAME_FONT =
  'var(--font-jetbrains-mono), "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';

export const PHOSPHOR_GLOW = "0 0 6px rgba(0,255,102,0.55)";

const ACCEPTED_EXTENSIONS = [".cbl", ".cob", ".txt"];

function hasAcceptedExtension(name: string): boolean {
  const lower = name.toLowerCase();
  return ACCEPTED_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

interface CobolSourceEditorProps {
  /** Current COBOL source. Sent as `cobol_code` on the next Resurrect. */
  source: string;
  onSourceChange: (source: string) => void;
  /** Bypass Session Cache (Force Live AI) checkbox state, sent as `force_live_llm`. */
  forceLiveLlm: boolean;
  onForceLiveLlmChange: (value: boolean) => void;
  /** Called when the user clears the editor or loads the sandbox attack snippet. */
  onCustomEdit?: () => void;
  disabled?: boolean;
}

export default function CobolSourceEditor({
  source,
  onSourceChange,
  forceLiveLlm,
  onForceLiveLlmChange,
  onCustomEdit,
  disabled = false,
}: CobolSourceEditorProps) {
  const gutterRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);

  const lineCount = source.split("\n").length;
  const lineNumbers = Array.from({ length: lineCount }, (_, i) => i + 1).join("\n");

  const loadFile = async (file: File) => {
    if (!hasAcceptedExtension(file.name)) {
      setFileError(`Unsupported file "${file.name}". Use .cbl, .cob or .txt.`);
      return;
    }
    try {
      const text = await file.text();
      setFileError(null);
      onSourceChange(text);
      onCustomEdit?.();
    } catch {
      setFileError(`Could not read "${file.name}".`);
    }
  };

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
    if (disabled) return;
    const file = e.dataTransfer.files?.[0];
    if (file) void loadFile(file);
  };

  const clearSource = () => {
    setFileError(null);
    onSourceChange("");
    onCustomEdit?.();
  };

  const loadSnippet = () => {
    setFileError(null);
    onSourceChange(SANDBOX_TEST_SNIPPET);
    onCustomEdit?.();
  };

  const btn =
    "rounded border border-[#00FF66]/60 px-2 py-1 text-xs uppercase tracking-wide text-[#00FF66] hover:bg-[#00FF66]/10 focus:outline-none focus:ring-1 focus:ring-[#00FF66] disabled:opacity-40";

  return (
    <section
      aria-label="COBOL source"
      data-testid="cobol-source-editor"
      className="flex h-full flex-col bg-[#0A0E0C] text-[#00FF66]"
      style={{ fontFamily: MAINFRAME_FONT, textShadow: PHOSPHOR_GLOW }}
    >
      <div className="flex flex-wrap items-center gap-2 border-b border-[#00FF66]/30 px-3 py-2">
        <span className="text-xs uppercase tracking-widest">COBOL SOURCE</span>
        <input
          ref={fileInputRef}
          type="file"
          accept=".cbl,.cob,.txt"
          data-testid="upload-input"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void loadFile(file);
            e.target.value = "";
          }}
        />
        <button
          type="button"
          className={btn}
          data-testid="upload-button"
          disabled={disabled}
          onClick={() => fileInputRef.current?.click()}
        >
          Upload .cbl / .cob File
        </button>
        <button
          type="button"
          className={btn}
          data-testid="clear-blank"
          disabled={disabled}
          onClick={clearSource}
        >
          Clear / Blank
        </button>
        <label className="flex cursor-pointer items-center gap-1 text-xs uppercase tracking-wide">
          <input
            type="checkbox"
            data-testid="force-live-checkbox"
            checked={forceLiveLlm}
            disabled={disabled}
            onChange={(e) => onForceLiveLlmChange(e.target.checked)}
            className="h-3 w-3 accent-[#00FF66]"
          />
          Bypass Session Cache (Force Live AI)
        </label>
        <button
          type="button"
          className={btn}
          data-testid="load-sandbox-snippet"
          disabled={disabled}
          onClick={loadSnippet}
        >
          Load Test Sandbox Attack (CALL SYSTEM)
        </button>
      </div>

      {fileError && (
        <div
          role="alert"
          data-testid="upload-error"
          className="border-b border-[#EF4444] px-3 py-1 text-xs text-[#EF4444]"
        >
          {fileError}
        </div>
      )}

      <div
        data-testid="cobol-terminal"
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={`relative flex min-h-[12rem] flex-1 overflow-hidden text-sm leading-5 ${
          dragging ? "ring-2 ring-inset ring-[#00FF66]" : ""
        }`}
      >
        <div
          ref={gutterRef}
          aria-hidden="true"
          className="select-none overflow-hidden whitespace-pre border-r border-[#00FF66]/30 px-2 py-2 text-right text-[#00FF66]/50"
        >
          {lineNumbers}
        </div>
        <textarea
          data-testid="cobol-textarea"
          aria-label="Edit COBOL source"
          spellCheck={false}
          wrap="off"
          placeholder="Paste COBOL, drop a .cbl/.cob file here, or pick a starter template."
          value={source}
          disabled={disabled}
          onChange={(e) => onSourceChange(e.target.value)}
          onScroll={(e) => {
            if (gutterRef.current)
              gutterRef.current.scrollTop = e.currentTarget.scrollTop;
          }}
          className="h-full min-h-[12rem] w-full flex-1 resize-none overflow-auto whitespace-pre bg-transparent px-3 py-2 leading-5 text-[#00FF66] caret-[#00FF66] outline-none placeholder:text-[#00FF66]/40"
          style={{ fontFamily: MAINFRAME_FONT, textShadow: PHOSPHOR_GLOW }}
        />
      </div>
    </section>
  );
}
