"use client";

// useResurrect: latest-wins request sequencing for POST /api/resurrect.
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import {
  ApiError,
  type ResurrectRequestBody,
  type ResurrectResponse,
} from "../lib/types";

export interface UseResurrectResult {
  run: (body: ResurrectRequestBody) => Promise<void>;
  loading: boolean;
  data: ResurrectResponse | null;
  error: ApiError | null;
}

export function useResurrect(): UseResurrectResult {
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<ResurrectResponse | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  const latestId = useRef(0);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => {
      // Invalidate in-flight requests and abort on unmount.
      latestId.current += 1;
      controllerRef.current?.abort();
    };
  }, []);

  const run = useCallback(async (body: ResurrectRequestBody): Promise<void> => {
    const id = ++latestId.current;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;

    setLoading(true);
    setError(null);

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

  return { run, loading, data, error };
}
