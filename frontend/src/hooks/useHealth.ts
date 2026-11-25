"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { HealthResponse } from "@/lib/types";

export const HEALTH_RETRY_MS = 3000;

/** "cobc (GnuCOBOL) 3.2.0" -> "GnuCOBOL 3.2.0 Online". */
export function formatHealthLabel(cobcVersion: string, cobcAvailable = true): string {
  if (!cobcAvailable) return "GnuCOBOL Unavailable";
  const m = /GnuCOBOL\)?\s*(\d+(?:\.\d+)*)/i.exec(cobcVersion);
  return m ? `GnuCOBOL ${m[1]} Online` : "GnuCOBOL Online";
}

/** connecting: no successful response yet. online: ok + cobc. degraded: ok but cobc missing. */
export type HealthState = "connecting" | "online" | "degraded";

export interface UseHealthResult {
  state: HealthState;
  health: HealthResponse | null;
  /** Number of failed or non-ok attempts so far (for long-wait messaging). */
  attempts: number;
  /** Pill text, e.g. "GnuCOBOL 3.2.0 Online"; empty until connected. */
  label: string;
}

export function useHealth(): UseHealthResult {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [attempts, setAttempts] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let controller: AbortController | null = null;

    const poll = async () => {
      controller = new AbortController();
      try {
        const res = await api.health(controller.signal);
        if (cancelled) return;
        if (res && res.status === "ok") {
          setHealth(res);
          return; // stop polling once healthy
        }
      } catch {
        if (cancelled) return;
      }
      setAttempts((n) => n + 1);
      timer = setTimeout(poll, HEALTH_RETRY_MS);
    };

    void poll();

    return () => {
      cancelled = true;
      if (timer !== null) clearTimeout(timer);
      controller?.abort();
    };
  }, []);

  const state: HealthState = health === null ? "connecting" : health.cobc_available ? "online" : "degraded";
  const label = health ? formatHealthLabel(health.cobc_version, health.cobc_available) : "";
  return { state, health, attempts, label };
}
