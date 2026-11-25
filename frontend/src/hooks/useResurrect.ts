"use client";

// useResurrect: latest-wins request sequencing for POST /api/resurrect.
import { useCallback, useEffect, useRef, useState } from "react";
import { api, describeApiError, type DescribedError } from "../lib/api";
import {
  ApiError,
  type ResurrectRequestBody,
  type ResurrectResponse,
  type StageName,
  type StageProgress,
} from "../lib/types";

export const STAGE_ORDER: StageName[] = ["legacy_parser", "cloud_architect", "parity_auditor"];
/** The backend is one request; the UI advances the running stage on this interval while waiting. */
export const STAGE_ADVANCE_MS = 4000;

export type StageStatusMap = Record<StageName, StageProgress>;

const IDLE: StageStatusMap = { legacy_parser: "pending", cloud_architect: "pending", parity_auditor: "pending" };

export interface UseResurrectResult {
  stageStatus: StageStatusMap;
  /** Stage currently running, or null. */
  currentStage: StageName | null;
  /** Mapped message for 400/503/etc, or null. */
  errorInfo: DescribedError | null;
  run: (body: ResurrectRequestBody) => Promise<void>;
  loading: boolean;
  data: ResurrectResponse | null;
  error: ApiError | null;
}

export function useResurrect(): UseResurrectResult {
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<ResurrectResponse | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  const [stageIdx, setStageIdx] = useState(-1);
  const latestId = useRef(0);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => {
      // Invalidate in-flight requests and abort on unmount.
      latestId.current += 1;
      controllerRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    if (!loading) return;
    const t = setInterval(() => setStageIdx((i) => Math.min(i + 1, STAGE_ORDER.length - 1)), STAGE_ADVANCE_MS);
    return () => clearInterval(t);
  }, [loading]);

  const run = useCallback(async (body: ResurrectRequestBody): Promise<void> => {
    const id = ++latestId.current;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;

    setLoading(true);
    setError(null);
    setStageIdx(0);

    try {
      const res = await api.resurrect(body, controller.signal);
      if (id !== latestId.current) return; // stale response
      setData(res);
      setLoading(false);
    } catch (e) {
      if (id !== latestId.current) return; // stale or aborted
      setData(null);
      setError(
        e instanceof ApiError
          ? e
          : new ApiError(0, e instanceof Error ? e.message : "Network error"),
      );
      setLoading(false);
    }
  }, []);

  let stageStatus: StageStatusMap = IDLE;
  if (loading) {
    stageStatus = { ...IDLE };
    STAGE_ORDER.forEach((s, i) => {
      stageStatus[s] = i < stageIdx ? "done" : i === stageIdx ? "running" : "pending";
    });
  } else if (data) {
    stageStatus = { ...IDLE };
    for (const s of STAGE_ORDER) {
      const rec = data.stages.find((r) => r.name === s);
      stageStatus[s] = !rec ? "done" : rec.status === "failed" ? "failed" : "done";
    }
  }
  const currentStage = loading ? STAGE_ORDER[Math.max(stageIdx, 0)] : null;
  const errorInfo = error ? describeApiError(error) : null;

  return { run, loading, data, error, errorInfo, stageStatus, currentStage };
}
