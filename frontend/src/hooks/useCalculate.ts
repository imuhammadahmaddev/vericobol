"use client";

// Requirement 9.3, 9.4, 9.8: debounced /api/calculate with cancel-and-restart
// and a latest-wins guard.
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { ApiError, type CalculateResponse, type MoneyString } from "../lib/types";

export const CALCULATE_DEBOUNCE_MS = 250;

export interface UseCalculate {
  trigger: (presetId: string, inputs: Record<string, MoneyString>) => void;
  cancel: () => void;
  loading: boolean;
  data: CalculateResponse | null;
  error: ApiError | Error | null;
}

export function useCalculate(): UseCalculate {
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<CalculateResponse | null>(null);
  const [error, setError] = useState<ApiError | Error | null>(null);

  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const latestIdRef = useRef(0);
  const mountedRef = useRef(true);

  const clearPending = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    if (controllerRef.current) {
      controllerRef.current.abort();
      controllerRef.current = null;
    }
  }, []);

  const trigger = useCallback(
    (presetId: string, inputs: Record<string, MoneyString>) => {
      // Cancel-and-restart: drop pending timer and any in-flight request.
      clearPending();
      const id = ++latestIdRef.current;
      timerRef.current = setTimeout(() => {
        timerRef.current = null;
        const controller = new AbortController();
        controllerRef.current = controller;
        setLoading(true);
        api
          .calculate({ preset_id: presetId, inputs }, controller.signal)
          .then((res) => {
            if (!mountedRef.current || id !== latestIdRef.current) return;
            setData(res);
            setError(null);
            setLoading(false);
          })
          .catch((err: unknown) => {
            if (!mountedRef.current || id !== latestIdRef.current) return;
            if (err instanceof DOMException && err.name === "AbortError") return;
            setError(err instanceof Error ? err : new Error(String(err)));
            setLoading(false);
          });
      }, CALCULATE_DEBOUNCE_MS);
    },
    [clearPending],
  );

  const cancel = useCallback(() => {
    latestIdRef.current++;
    clearPending();
    setLoading(false);
  }, [clearPending]);

  useEffect(() => {
    mountedRef.current = true;
    const latestId = latestIdRef;
    return () => {
      mountedRef.current = false;
      latestId.current++;
      clearPending();
    };
  }, [clearPending]);

  return { trigger, cancel, loading, data, error };
}
