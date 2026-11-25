"use client";

// Requirement 9.3, 9.4, 9.8: debounced /api/calculate with cancel-and-restart
// and a latest-wins guard.
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import {
  ApiError,
  type CalculateCustomRequestBody,
  type CalculateResponse,
  type MoneyString,
} from "../lib/types";

/** Everything except `inputs`; passing this object selects /api/calculate-custom. */
export type CustomTarget = Omit<CalculateCustomRequestBody, "inputs">;

export const CALCULATE_DEBOUNCE_MS = 250;

export interface UseCalculate {
  /** string = legacy preset id; object = custom program (values stay decimal strings). */
  trigger: (target: string | CustomTarget, inputs: Record<string, MoneyString>) => void;
  /** Latest raw GnuCOBOL stdout, for the Legacy_Panel. */
  stdout: string;
  parityVerified: boolean | null;
  cancel: () => void;
  loading: boolean;
  data: CalculateResponse | null;
  error: ApiError | Error | null;
}

export interface UseCalculateOptions {
  onStdout?: (stdout: string) => void;
}

export function useCalculate(options: UseCalculateOptions = {}): UseCalculate {
  const onStdoutRef = useRef(options.onStdout);
  onStdoutRef.current = options.onStdout;
  const [parityVerified, setParityVerified] = useState<boolean | null>(null);
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
    (target: string | CustomTarget, inputs: Record<string, MoneyString>) => {
      // Cancel-and-restart: drop pending timer and any in-flight request.
      clearPending();
      const id = ++latestIdRef.current;
      timerRef.current = setTimeout(() => {
        timerRef.current = null;
        const controller = new AbortController();
        controllerRef.current = controller;
        setLoading(true);
        const call: Promise<CalculateResponse> =
          typeof target === "string"
            ? api.calculate({ preset_id: target, inputs }, controller.signal)
            : api.calculateCustom({ ...target, inputs }, controller.signal).then((r) => {
                setParityVerified(r.parity_verified);
                return { preset_id: "custom", result: r.result, raw_cobol_output: r.raw_cobol_output };
              });
        call
          .then((res) => {
            if (!mountedRef.current || id !== latestIdRef.current) return;
            setData(res);
            onStdoutRef.current?.(res.raw_cobol_output);
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

  return { trigger, cancel, loading, data, error, stdout: data?.raw_cobol_output ?? "", parityVerified };
}
