"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { ApiError } from "@/lib/types";
import type { ContractSchema, IoProtocol } from "@/lib/types";

interface ExportTabProps {
  cobolCode: string;
  /** `verified_python_code` from the resurrect response. */
  pythonCode: string;
  schema: ContractSchema;
  ioProtocol: IoProtocol;
  /** `export_fastapi_code` from the resurrect response, shown before any refresh. */
  initialCode?: string;
  /** Used for the fallback download name. */
  programTitle?: string;
  /** False when parity verification failed; shows a warning banner. */
  certified?: boolean;
}

/** Extract the error code from an API error body, falling back to the HTTP status. */
function errorCode(err: unknown): string {
  if (err instanceof ApiError) {
    const d: unknown = err.detail;
    if (typeof d === "object" && d !== null && !Array.isArray(d)) {
      const o = d as { error?: unknown; message?: unknown };
      if (typeof o.message === "string") return o.message;
      if (typeof o.error === "string") return o.error;
    }
    return typeof d === "string" && d ? `HTTP ${err.status}: ${d}` : `HTTP ${err.status}`;
  }
  return err instanceof Error ? err.message : "unknown_error";
}

function fallbackFilename(title: string | undefined): string {
  const slug = (title ?? "program").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  return `${slug || "program"}_api.py`;
}

/** Export_Tab: shows export_fastapi_code, refreshes it via POST /api/export-custom, copy and download. */
export default function ExportTab({
  cobolCode,
  pythonCode,
  schema,
  ioProtocol,
  initialCode,
  programTitle,
  certified,
}: ExportTabProps) {
  const [code, setCode] = useState<string | null>(initialCode ?? null);
  const [filename, setFilename] = useState<string>(fallbackFilename(programTitle));
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);

  // A new resurrect result replaces whatever was shown.
  useEffect(() => {
    setCode(initialCode ?? null);
    setFilename(fallbackFilename(programTitle));
    setError(null);
  }, [initialCode, programTitle]);

  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true);
      setError(null);
      try {
        const res = await api.exportCustom(
          { cobol_code: cobolCode, python_code: pythonCode, schema, io_protocol: ioProtocol },
          signal,
        );
        if (signal?.aborted) return;
        setCode(res.export_fastapi_code);
        if (res.filename) setFilename(res.filename);
      } catch (err: unknown) {
        if (signal?.aborted) return;
        setError(errorCode(err));
      } finally {
        if (!signal?.aborted) setLoading(false);
      }
    },
    [cobolCode, pythonCode, schema, ioProtocol],
  );

  // Without initial code, fetch on mount.
  useEffect(() => {
    if (initialCode) return;
    const ctrl = new AbortController();
    void refresh(ctrl.signal);
    return () => ctrl.abort();
  }, [initialCode, refresh]);

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
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }, [code, filename]);

  return (
    <div className="space-y-3 rounded-xl border border-slate-800 bg-[#0F172A] p-3" data-testid="export-tab">
      {certified === false && (
        <div
          role="alert"
          data-testid="export-not-certified"
          className="rounded-lg border border-[#EF4444] bg-[#EF4444]/10 p-3 text-sm font-semibold text-[#EF4444]"
        >
          Not certified — fix parity mismatches before deploying this microservice
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={loading}
          className="rounded-lg border border-slate-700 bg-[#1E293B] px-3 py-1.5 text-sm font-medium text-slate-100 hover:bg-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Refresh Export
        </button>
        <button
          type="button"
          onClick={onCopy}
          disabled={code === null}
          className="rounded-lg border border-slate-700 bg-[#1E293B] px-3 py-1.5 text-sm font-medium text-slate-100 hover:bg-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
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
        <span role="status" aria-live="polite" className="text-sm text-emerald-400">
          {copied ? "Copied to clipboard" : ""}
        </span>
      </div>

      {loading && <p className="text-sm text-slate-400">Loading FastAPI service…</p>}

      {error !== null && (
        <div
          role="alert"
          data-testid="export-error"
          className="rounded-lg border border-[#EF4444] bg-[#EF4444]/10 p-3 text-sm text-[#EF4444]"
        >
          Export failed: <code data-testid="export-error-code">{error}</code>
        </div>
      )}

      {code !== null && (
        <pre
          data-testid="export-code"
          tabIndex={0}
          className="max-h-96 overflow-auto rounded-lg bg-[#1E293B] p-4 font-mono text-xs leading-relaxed text-slate-100"
        >
          <code>{code}</code>
        </pre>
      )}
    </div>
  );
}
