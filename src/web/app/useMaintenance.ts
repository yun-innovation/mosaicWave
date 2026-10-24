"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { apiFetch, MAINTENANCE_EVENT } from "./api";
import { isAbortError, MaintenanceStatus } from "./types";

const IDLE_MS = 15000;
const ACTIVE_MS = 3000;

const OFF: MaintenanceStatus = { state: "off", reason: "", since: null };

export type Maintenance = {
  status: MaintenanceStatus;
  /** False until the first answer (or failure), so the page does not flash before it knows. */
  ready: boolean;
  /** Look now instead of waiting for the next poll. */
  refresh: () => void;
  /** Use an answer we already have (right after the admin switched it). */
  set: (status: MaintenanceStatus) => void;
};

/**
 * Follows `GET /api/v1/maintenance` (public, so it also works signed out): every 15 s normally,
 * every 3 s while maintenance is on or starting, and at once when any call is refused with
 * `503 maintenance`.
 */
export function useMaintenance(): Maintenance {
  const [status, setStatus] = useState<MaintenanceStatus>(OFF);
  const [ready, setReady] = useState(false);
  const poke = useRef<() => void>(() => undefined);

  useEffect(() => {
    const ctrl = new AbortController();
    let timer: number | undefined;
    let inFlight = false;

    async function tick() {
      if (inFlight) return;
      inFlight = true;
      window.clearTimeout(timer);
      let next = IDLE_MS;
      try {
        const res = await apiFetch("/api/v1/maintenance", { signal: ctrl.signal });
        if (res.ok) {
          const body = (await res.json()) as MaintenanceStatus;
          setStatus((prev) =>
            prev.state === body.state && prev.reason === body.reason && prev.since === body.since ? prev : body,
          );
          if (body.state !== "off") next = ACTIVE_MS;
        }
      } catch (err: unknown) {
        if (isAbortError(err)) return;
        // Server restarting or unreachable: keep the last answer and try again.
      } finally {
        inFlight = false;
      }
      setReady(true);
      if (!ctrl.signal.aborted) timer = window.setTimeout(() => void tick(), next);
    }

    poke.current = () => void tick();
    const onRefused = () => void tick();
    window.addEventListener(MAINTENANCE_EVENT, onRefused);
    void tick();
    return () => {
      ctrl.abort();
      window.clearTimeout(timer);
      window.removeEventListener(MAINTENANCE_EVENT, onRefused);
    };
  }, []);

  const refresh = useCallback(() => poke.current(), []);
  return { status, ready, refresh, set: setStatus };
}
