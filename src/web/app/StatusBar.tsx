"use client";

import { useEffect } from "react";
import { useTranslation } from "react-i18next";

import { CancelJobButton } from "./CancelJobButton";
import { formatNumber } from "./format";
import { routeHash } from "./hashRoute";
import { ImportJob } from "./types";
import { FinishedJob, jobKindLabel } from "./useActiveJob";

const DONE_VISIBLE_MS = 8000;

type Props = {
  job: ImportJob | null;
  finished: FinishedJob | null;
  onDismiss: () => void;
  /** Look at the jobs again now (after a cancel request). */
  onRefresh: () => void;
};

/**
 * One place for background task progress, on every view: the running job (any kind) with its
 * count, then the result of the last one. Successes clear themselves; failures stay until dismissed.
 */
export function StatusBar({ job, finished, onDismiss, onRefresh }: Props) {
  const { t } = useTranslation(["jobbar", "common"]);
  const clearAfter = !job && finished?.ok ? finished : null;

  useEffect(() => {
    if (!clearAfter) return;
    const timer = window.setTimeout(onDismiss, DONE_VISIBLE_MS);
    return () => window.clearTimeout(timer);
  }, [clearAfter, onDismiss]);

  const activity = routeHash({ view: "activity", albumId: null });

  if (job) {
    const known = job.total > 0;
    return (
      <div className="status-bar" role="status" aria-live="polite">
        <span className="status-bar-label">{jobKindLabel(job.kind)}</span>
        <span className="status-bar-count">
          {known
            ? job.kind === "data_dir_move"
              ? t("jobbar:mb", { processed: formatNumber(job.processed), total: formatNumber(job.total) })
              : `${formatNumber(job.processed)} / ${formatNumber(job.total)}`
            : t("jobbar:starting")}
        </span>
        {job.current && <span className="status-bar-current">{job.current}</span>}
        {known ? (
          <progress className="status-bar-meter" max={job.total} value={Math.min(job.processed, job.total)} />
        ) : (
          <progress className="status-bar-meter" />
        )}
        <a href={activity}>{t("jobbar:viewActivity")}</a>
        {job.id && !(job.mode === "push" && job.phase === "receiving") && (
          <CancelJobButton jobId={job.id} kind={job.kind} onRequested={onRefresh} />
        )}
      </div>
    );
  }

  if (finished) {
    const tone = finished.cancelled ? "status-bar" : finished.ok ? "status-bar done" : "status-bar fail";
    return (
      <div className={tone} role="status" aria-live="polite">
        <span className="status-bar-label">{jobKindLabel(finished.kind)}</span>
        <span className="status-bar-current">{finished.text}</span>
        <a href={activity}>{t("jobbar:viewActivity")}</a>
        <button type="button" onClick={onDismiss}>
          {t("jobbar:dismiss")}
        </button>
      </div>
    );
  }
  return null;
}
