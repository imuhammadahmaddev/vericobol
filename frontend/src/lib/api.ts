// API_Client: the only module in the frontend that calls fetch().
import {
  ApiError,
  type CalculateCustomRequestBody,
  type CalculateCustomResponse,
  type CalculateRequestBody,
  type CalculateResponse,
  type ErrorDetail,
  type ExportCustomRequestBody,
  type ExportCustomResponse,
  type ExportResponse,
  type HealthResponse,
  type StarterTemplate,
  type ResurrectRequestBody,
  type ResurrectResponse,
  type VerifyRequestBody,
  type VerifyResponse,
} from "./types";

const BASE_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/+$/, "");

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try {
      // JSON.parse keeps quoted decimal strings as strings; no numeric coercion.
      body = JSON.parse(text);
    } catch {
      if (!res.ok) throw new ApiError(res.status, text);
      throw new ApiError(res.status, "Invalid JSON in response");
    }
  }
  if (!res.ok) {
    const obj = body as { detail?: ErrorDetail } | null;
    const detail = (obj && typeof obj === "object" && "detail" in obj ? obj.detail : body) as ErrorDetail;
    throw new ApiError(res.status, detail ?? `HTTP ${res.status}`);
  }
  return body as T;
}

export const api = {
  health: (signal?: AbortSignal) => request<HealthResponse>("/health", { signal }),
  presets: (signal?: AbortSignal) => request<StarterTemplate[]>("/api/presets", { signal }),
  calculate: (body: CalculateRequestBody, signal?: AbortSignal) =>
    request<CalculateResponse>("/api/calculate", { method: "POST", body: JSON.stringify(body), signal }),
  calculateCustom: (body: CalculateCustomRequestBody, signal?: AbortSignal) =>
    request<CalculateCustomResponse>("/api/calculate-custom", {
      method: "POST",
      body: JSON.stringify(body),
      signal,
    }),
  exportCustom: (body: ExportCustomRequestBody, signal?: AbortSignal) =>
    request<ExportCustomResponse>("/api/export-custom", {
      method: "POST",
      body: JSON.stringify(body),
      signal,
    }),
  resurrect: (body: ResurrectRequestBody, signal?: AbortSignal) =>
    request<ResurrectResponse>("/api/resurrect", { method: "POST", body: JSON.stringify(body), signal }),
  verify: (body: VerifyRequestBody, signal?: AbortSignal) =>
    request<VerifyResponse>("/api/verify", { method: "POST", body: JSON.stringify(body), signal }),
  exportPreset: (presetId: string, signal?: AbortSignal) =>
    request<ExportResponse>(`/api/export/${encodeURIComponent(presetId)}`, { signal }),
};

export { request };

export type ApiErrorKind = "rejected" | "llm_unavailable" | "llm_failed" | "invalid" | "network" | "other";

export interface DescribedError {
  kind: ApiErrorKind;
  status: number;
  /** Message to show verbatim; no results are rendered for 400/503. */
  message: string;
}

/** Map an ApiError to the message the UI shows (400 sandbox/compile, 503 LLM unavailable, ...). */
export function describeApiError(err: ApiError): DescribedError {
  const d = err.detail;
  let message = `Request failed (HTTP ${err.status}).`;
  if (typeof d === "string") {
    message = d;
  } else if (Array.isArray(d)) {
    message = d.map((e) => e.msg).join("; ");
  } else if (d && typeof d === "object") {
    const o = d as { message?: unknown; error?: unknown; token?: unknown; line?: unknown };
    if (typeof o.message === "string") {
      message = o.message;
    } else if (typeof o.error === "string") {
      message = o.error;
    }
    if (err.status === 400 && typeof o.token === "string" && typeof o.line === "number") {
      message = `${message} (${o.token}, line ${o.line})`;
    }
  }
  let kind: ApiErrorKind = "other";
  if (err.status === 0) kind = "network";
  else if (err.status === 400 || err.status === 413) kind = "rejected";
  else if (err.status === 503) kind = "llm_unavailable";
  else if (err.status === 502) kind = "llm_failed";
  else if (err.status === 422) kind = "invalid";
  return { kind, status: err.status, message };
}
