"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { apiFetch, Me } from "./api";
import i18n from "./i18n";
import { ImportJob, isAbortError } from "./types";

const BUSY_MS = 1000;
const IDLE_MS = 4000;

/** What the status bar shows once a job it was following has ended. */
export type FinishedJob = {
  id: string;
  kind: string | null;
  ok: boolean;
  /** Stopped on request: neither a success nor a failure. */
  cancelled?: boolean;
  text: string;
};

export type ActiveJobs = {
  /** The queued or running job, if any. One job runs at a time on the server. */
  job: ImportJob | null;
  finished: FinishedJob | null;
  dismissFinished: () => void;
  /** Look now instead of waiting for the next poll (call right after starting a job). */
  refresh: () => void;
};

export function isActive(job: ImportJob | null | undefined): job is ImportJob {
  return !!job && (job.status === "running" || job.status === "queued");
}

/** `Thumbnails`, `Site backup`, … for a job kind. */
export function jobKindLabel(kind: string | null): string {
  if (kind === "takeout_import") return i18n.t("import:jobs.kind.takeout_import");
  if (kind === "thumb_batch") return i18n.t("import:jobs.kind.thumb_batch");
  if (kind === "site_backup") return i18n.t("import:jobs.kind.site_backup");
  if (kind === "site_restore") return i18n.t("import:jobs.kind.site_restore");
  if (kind === "library_backup") return i18n.t("import:jobs.kind.library_backup");
  if (kind === "library_restore") return i18n.t("import:jobs.kind.library_restore");
  return kind || i18n.t("import:jobs.kind.unknown");
}

function summary(job: ImportJob): FinishedJob {
  const id = job.id ?? "";
  if (job.status === "cancelled") {
    const text =
      job.total > 0
        ? i18n.t("jobbar:cancelledAt", { processed: job.processed, total: job.total })
        : i18n.t("jobbar:cancelled");
    return { id, kind: job.kind, ok: true, cancelled: true, text };
  }
  if (job.status === "error") {
    return { id, kind: job.kind, ok: false, text: job.error || i18n.t("jobbar:failed") };
  }
  if (job.kind === "thumb_batch") {
    return { id, kind: job.kind, ok: true, text: i18n.t("import:result.thumbsReady", { count: job.processed }) };
  }
  if (job.kind === "site_backup") {
    return { id, kind: job.kind, ok: true, text: i18n.t("jobbar:backupDone") };
  }
  if (job.kind === "site_restore") {
    return { id, kind: job.kind, ok: true, text: i18n.t("jobbar:restoreStaged") };
  }
  if (job.kind === "library_backup") {
    return { id, kind: job.kind, ok: true, text: i18n.t("jobbar:libraryBackupDone") };
  }
  if (job.kind === "library_restore") {
    return { id, kind: job.kind, ok: true, text: i18n.t("jobbar:libraryRestored") };
  }
  const counts = {
    assets: i18n.t("import:result.assets", { count: job.assets }),
    albums: i18n.t("import:result.albums", { count: job.albums }),
  };
  return { id, kind: job.kind, ok: true, text: i18n.t("import:result.imported", counts) };
}

/**
 * Follows the server's jobs for the signed-in user, whatever started them (this tab, another tab,
 * before a reload or a sign-out). Polls only while signed in. `onFinished` runs once per job the
 * hook saw running that has now ended.
 */
export function useActiveJob(me: Me | null, onFinished: (job: ImportJob) => void): ActiveJobs {
  const [job, setJob] = useState<ImportJob | null>(null);
  const [finished, setFinished] = useState<FinishedJob | null>(null);
  const poke = useRef<() => void>(() => undefined);
  const onFinishedRef = useRef(onFinished);
  onFinishedRef.current = onFinished;
  const signedIn = me !== null;

  useEffect(() => {
    if (!signedIn) {
      setJob(null);
      setFinished(null);
      poke.current = () => undefined;
      return;
    }
    const ctrl = new AbortController();
    let timer: number | undefined;
    let followed: ImportJob | null = null;

    async function tick() {
      window.clearTimeout(timer);
      let next = IDLE_MS;
      try {
        const res = await apiFetch("/api/v1/jobs?limit=1", { signal: ctrl.signal });
        if (res.ok) {
          const first = ((await res.json()) as { items: ImportJob[] }).items[0] ?? null;
          const active = isActive(first) ? first : null;
          if (followed && (!active || active.id !== followed.id)) {
            let last: ImportJob | null = first && first.id === followed.id ? first : null;
            if (!last) {
              const one = await apiFetch(`/api/v1/jobs/${followed.id}`, { signal: ctrl.signal });
              if (one.ok) last = (await one.json()) as ImportJob;
            }
            if (last) {
              setFinished(summary(last));
              onFinishedRef.current(last);
            }
          }
          followed = active;
          setJob(active);
          if (active) {
            setFinished(null);
            next = BUSY_MS;
          }
        }
      } catch (err: unknown) {
        if (isAbortError(err)) return;
      }
      if (!ctrl.signal.aborted) timer = window.setTimeout(() => void tick(), next);
    }

    poke.current = () => void tick();
    void tick();
    return () => {
      ctrl.abort();
      window.clearTimeout(timer);
    };
  }, [signedIn]);

  const dismissFinished = useCallback(() => setFinished(null), []);
  const refresh = useCallback(() => poke.current(), []);
  return { job, finished, dismissFinished, refresh };
}
