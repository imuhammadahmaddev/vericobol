"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { ApiError } from "@/lib/types";

interface ExportTabProps {
  presetId: string;
}

/** Extract the error code from an API error body, falling back to the HTTP status. */
function errorCode(err: unknown): string {
  if (err instanceof ApiError) {
    const d: unknown = err.detail;
    if (typeof d === "object" && d !== null && !Array.isArray(d)) {
      const code = (d as { error?: unknown }).error;
      if (typeof code === "string") return code;
    }
    return typeof d === "string" && d ? `HTTP ${err.status}: ${d}` : `HTTP ${err.status}`;
  }
  return err instanceof Error ? err.message : "unknown_error";
}

/** Export_Tab: shows export_fastapi_code from GET /api/export/{preset_id}. Mount it when the tab is opened. */
export default function ExportTab({ presetId }: ExportTabProps) {
  const [code, setCode] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    setCode(null);
    api
      .exportPreset(presetId, ctrl.signal)
      .then((res) => setCode(res.export_fastapi_code))
      .catch((err: unknown) => {
        if (ctrl.signal.aborted) return;
        setError(errorCode(err));
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false);
      });
    return () => ctrl.abort();
  }, [presetId]);

  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(t);
  }, [copied]);

  const onCopy = useCallback(async () => {
    if (code === null) return;
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }, [code]);

  const onDownload = useCallback(() => {
    if (code === null) return;
    const blob = new Blob([code], { type: "text/x-python" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${presetId}_api.py`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }, [code, presetId]);

  return (
    <div className="space-y-3" data-testid="export-tab">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={onCopy}
          disabled={code === null}
          className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Copy
        </button>
        <button
          type="button"
          onClick={onDownload}
          disabled={code === null}
          className="rounded-lg bg-[#10B981] px-3 py-1.5 text-sm font-semibold text-white hover:bg-emerald-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Download Verified Service (.py)
        </button>
        <span role="status" aria-live="polite" className="text-sm text-emerald-700">
          {copied ? "Copied to clipboard" : ""}
        </span>
      </div>

      {loading && <p className="text-sm text-slate-500">Loading FastAPI service…</p>}

      {error !== null && (
        <div
          role="alert"
          data-testid="export-error"
          className="rounded-lg border border-[#EF4444] bg-red-50 p-3 text-sm text-[#B91C1C]"
        >
          Export failed: <code data-testid="export-error-code">{error}</code>
        </div>
      )}

      {code !== null && (
        <pre
          data-testid="export-code"
          tabIndex={0}
          className="max-h-96 overflow-auto rounded-lg bg-slate-900 p-4 font-mono text-xs leading-relaxed text-slate-100"
        >
          <code>{code}</code>
        </pre>
      )}
    </div>
  );
}
