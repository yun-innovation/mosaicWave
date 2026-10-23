"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { apiFetch } from "./api";
import { formatDateTime } from "./format";
import i18n from "./i18n";
import { ImportJob, ignoreAbort, isAbortError } from "./types";
import { jobKindLabel } from "./useActiveJob";
import { TakeoutImport } from "./useTakeoutImport";

const JOB_STATUSES = ["idle", "queued", "running", "done", "error"] as const;

function jobStatusLabel(status: string): string {
  const known = JOB_STATUSES.find((s) => s === status);
  return known ? i18n.t(`import:jobs.status.${known}`) : status;
}

type Props = { importer: TakeoutImport; libraryRev: number };

export function ActivityView({ importer, libraryRev }: Props) {
  const { t } = useTranslation(["import"]);
  const [jobs, setJobs] = useState<ImportJob[] | null>(null);
  const { busy } = importer;

  useEffect(() => {
    const ctrl = new AbortController();
    async function load() {
      try {
        const res = await apiFetch("/api/v1/jobs", { signal: ctrl.signal });
        if (!res.ok) return;
        const body = (await res.json()) as { items: ImportJob[] };
        setJobs(body.items);
      } catch (err: unknown) {
        if (isAbortError(err)) return;
      }
    }
    ignoreAbort(load());
    const timer = busy ? window.setInterval(() => ignoreAbort(load()), 3000) : undefined;
    return () => {
      window.clearInterval(timer);
      ctrl.abort();
    };
  }, [libraryRev, busy]);

  return (
    <section className="card">
      <div className="gallery-head">
        <h2>{t("import:jobs.title")}</h2>
      </div>
      {jobs === null && <p className="detail">{t("import:jobs.loading")}</p>}
      {jobs && jobs.length === 0 && <p className="detail">{t("import:jobs.empty")}</p>}
      {jobs && jobs.length > 0 && (
        <ul className="asset-list">
          {jobs.map((job) => (
            <li key={job.id ?? `${job.created_at}-${job.status}`}>
              <span className="name">
                {jobKindLabel(job.kind)} · {jobStatusLabel(job.status)}
                {job.status === "done" && job.kind === "thumb_batch" && job.processed
                  ? ` · ${t("import:jobs.thumbs", { count: job.processed })}`
                  : ""}
                {job.status === "done" && job.kind !== "thumb_batch" && job.assets
                  ? ` · ${t("import:result.assets", { count: job.assets })}`
                  : ""}
                {job.error ? ` · ${job.error}` : ""}
              </span>
              <span className="date">{formatDateTime(job.started_at || job.created_at)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
