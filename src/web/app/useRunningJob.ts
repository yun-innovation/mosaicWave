"use client";

import { useEffect, useState } from "react";

import { apiFetch } from "./api";
import { formatNumber } from "./format";
import i18n from "./i18n";
import { isAbortError, RunningJob } from "./types";
import { jobKindLabel } from "./useActiveJob";

const POLL_MS = 2000;

/**
 * The job maintenance mode is waiting for, whoever started it (admin only). Polls only while
 * `enabled` (an admin, maintenance starting); the answer is cleared when it stops.
 */
export function useRunningJob(enabled: boolean): RunningJob | null {
  const [job, setJob] = useState<RunningJob | null>(null);

  useEffect(() => {
    if (!enabled) {
      setJob(null);
      return;
    }
    const ctrl = new AbortController();
    let timer: number | undefined;

    async function tick() {
      try {
        const res = await apiFetch("/api/v1/jobs/running", { signal: ctrl.signal });
        if (res.ok) setJob(((await res.json()) as { job: RunningJob | null }).job);
      } catch (err: unknown) {
        if (isAbortError(err)) return;
      }
      if (!ctrl.signal.aborted) timer = window.setTimeout(() => void tick(), POLL_MS);
    }

    void tick();
    return () => {
      ctrl.abort();
      window.clearTimeout(timer);
    };
  }, [enabled]);

  return job;
}

/** `Thumbnails (Pat)`: what is running and for whom. */
export function runningLabel(job: RunningJob): string {
  const kind = jobKindLabel(job.kind);
  return job.owner ? i18n.t("maintenance:waiting.job", { kind, owner: job.owner }) : kind;
}

/** `1,200 / 8,000`, or null while the total is unknown. */
export function runningProgress(job: RunningJob): string | null {
  return job.total > 0 ? `${formatNumber(job.processed)} / ${formatNumber(job.total)}` : null;
}
