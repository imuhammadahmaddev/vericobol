"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { ResurrectResponse } from "@/lib/types";

interface VerifiedPythonTabProps {
  result: Pick<ResurrectResponse, "preset_id" | "verified_python_code" | "final_report">;
}

const KEYWORDS = new Set([
  "def", "class", "return", "if", "elif", "else", "for", "while", "in", "not", "and", "or",
  "import", "from", "as", "with", "try", "except", "finally", "raise", "pass", "None",
  "True", "False", "lambda", "is", "assert", "yield", "global", "nonlocal", "del", "break",
  "continue", "async", "await",
]);

// Order matters: comments, strings, numbers, identifiers.
const TOKEN_RE =
  /(#.*$)|("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')|(\b\d+(?:_\d+)*(?:\.\d+)?\b)|(@?[A-Za-z_]\w*)/gm;

/** Tiny dependency-free Python tokenizer. Returns React nodes (no HTML injection). */
export function highlightPython(code: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let key = 0;
  for (const m of code.matchAll(TOKEN_RE)) {
    const idx = m.index ?? 0;
    if (idx > last) out.push(code.slice(last, idx));
    const text = m[0];
    let cls = "";
    if (m[1]) cls = "text-slate-500 italic";
    else if (m[2]) cls = "text-emerald-300";
    else if (m[3]) cls = "text-amber-300";
    else if (text.startsWith("@")) cls = "text-fuchsia-300";
    else if (KEYWORDS.has(text)) cls = "text-sky-300 font-medium";
    if (cls) {
      out.push(
        <span key={key++} className={cls}>
          {text}
        </span>,
      );
    } else {
      out.push(text);
    }
    last = idx + text.length;
  }
  if (last < code.length) out.push(code.slice(last));
  return out;
}

export default function VerifiedPythonTab({ result }: VerifiedPythonTabProps) {
  const { verified_python_code: code, final_report: report, preset_id } = result;
  const [copied, setCopied] = useState<"idle" | "ok" | "error">("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const flash = (state: "ok" | "error") => {
    setCopied(state);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied("idle"), 2000);
  };

  const onCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(code);
      flash("ok");
    } catch {
      flash("error");
    }
  }, [code]);

  const onDownload = useCallback(() => {
    const name = `${preset_id ?? "custom"}.py`;
    const blob = new Blob([code], { type: "text/x-python;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }, [code, preset_id]);

  const certified = report?.certified === true;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {certified && (
          <span
            data-testid="zero-floats-badge"
            className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-700 ring-1 ring-[#10B981]"
          >
            <span aria-hidden="true">✓</span> 0 floats verified
          </span>
        )}
        <div className="ml-auto flex items-center gap-2">
          <span role="status" aria-live="polite" className="text-xs text-slate-600">
            {copied === "ok" && "Copied to clipboard"}
            {copied === "error" && "Copy failed"}
          </span>
          <button
            type="button"
            onClick={onCopy}
            className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-800 hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#10B981]"
          >
            Copy
          </button>
          <button
            type="button"
            onClick={onDownload}
            className="rounded-lg bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#10B981]"
          >
            Download .py
          </button>
        </div>
      </div>
      <pre
        tabIndex={0}
        aria-label="Verified Python source"
        className="max-h-[32rem] overflow-auto rounded-xl bg-slate-900 p-4 font-mono text-xs leading-relaxed text-slate-100"
      >
        <code>{highlightPython(code)}</code>
      </pre>
    </div>
  );
}
