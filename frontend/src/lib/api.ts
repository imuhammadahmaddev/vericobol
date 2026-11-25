// API_Client: the only module in the frontend that calls fetch().
import {
  ApiError,
  type CalculateRequestBody,
  type CalculateResponse,
  type ErrorDetail,
  type ExportResponse,
  type HealthResponse,
  type PresetSummary,
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
  presets: (signal?: AbortSignal) => request<PresetSummary[]>("/api/presets", { signal }),
  calculate: (body: CalculateRequestBody, signal?: AbortSignal) =>
    request<CalculateResponse>("/api/calculate", { method: "POST", body: JSON.stringify(body), signal }),
  resurrect: (body: ResurrectRequestBody, signal?: AbortSignal) =>
    request<ResurrectResponse>("/api/resurrect", { method: "POST", body: JSON.stringify(body), signal }),
  verify: (body: VerifyRequestBody, signal?: AbortSignal) =>
    request<VerifyResponse>("/api/verify", { method: "POST", body: JSON.stringify(body), signal }),
  exportPreset: (presetId: string, signal?: AbortSignal) =>
    request<ExportResponse>(`/api/export/${encodeURIComponent(presetId)}`, { signal }),
};

export { request };
