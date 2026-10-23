"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { apiFetch, apiMediaUrl, errorText, HostSettings, readDetail } from "./api";
import { formatBytes, formatDateTime } from "./format";
import i18n from "./i18n";
import { BackupItem, ImportJob, isAbortError, Notice } from "./types";

const SAMPLE_NAME = "mosaicwave-site-YYYYMMDD-HHMMSS.zip";

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Admin: site backup (start, progress, list, download, delete). Restore is offline; the page shows the command. */
export function BackupView() {
  const { t } = useTranslation(["backup", "common"]);
  const [items, setItems] = useState<BackupItem[] | null>(null);
  const [folder, setFolder] = useState<string | null>(null);
  const [withThumbs, setWithThumbs] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);

  const loadList = useCallback(async (signal?: AbortSignal) => {
    const res = await apiFetch("/api/v1/backups", { signal });
    if (!res.ok) {
      setItems([]);
      setNotice({ text: await readDetail(res), ok: false });
      return;
    }
    setItems((await res.json()) as BackupItem[]);
  }, []);

  /** Poll a backup job until it ends, then refresh the list. */
  const follow = useCallback(
    async (first: ImportJob, signal: AbortSignal) => {
      let job = first;
      while (job.status === "running" || job.status === "queued") {
        setProgress(
          job.total
            ? i18n.t("backup:progress", { processed: job.processed, total: job.total })
            : i18n.t("backup:starting"),
        );
        await sleep(1000);
        const res = await apiFetch(`/api/v1/jobs/${job.id}`, { signal });
        if (!res.ok) {
          setNotice({ text: await readDetail(res), ok: false });
          return;
        }
        job = (await res.json()) as ImportJob;
      }
      if (job.status === "done") {
        setNotice({ text: i18n.t("backup:done"), ok: true });
      } else {
        setNotice({ text: job.error || i18n.t("backup:failed"), ok: false });
      }
      await loadList(signal);
    },
    [loadList],
  );

  useEffect(() => {
    const ctrl = new AbortController();
    abort.current = ctrl;
    async function init() {
      try {
        await loadList(ctrl.signal);
        const host = await apiFetch("/api/v1/settings", { signal: ctrl.signal });
        if (host.ok) setFolder(((await host.json()) as HostSettings).backup_dir);
        // A backup started earlier (or in another tab) keeps running: pick it up.
        const jobs = await apiFetch("/api/v1/jobs?limit=1&kind=site_backup", { signal: ctrl.signal });
        if (!jobs.ok) return;
        const latest = ((await jobs.json()) as { items: ImportJob[] }).items[0];
        if (latest && (latest.status === "running" || latest.status === "queued")) {
          setBusy(true);
          try {
            await follow(latest, ctrl.signal);
          } finally {
            setBusy(false);
            setProgress(null);
          }
        }
      } catch (err: unknown) {
        if (isAbortError(err)) return;
        setItems((prev) => prev ?? []);
        setNotice({ text: err instanceof Error ? err.message : i18n.t("common:networkError"), ok: false });
      }
    }
    void init();
    return () => ctrl.abort();
  }, [loadList, follow]);

  async function start() {
    const signal = abort.current?.signal ?? new AbortController().signal;
    setBusy(true);
    setNotice(null);
    setProgress(i18n.t("backup:starting"));
    try {
      const res = await apiFetch("/api/v1/backups", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ include_thumbs: withThumbs }),
      });
      const body: unknown = await res.json().catch(() => null);
      if (!res.ok) {
        setNotice({ text: errorText(body, res.status), ok: false });
        return;
      }
      await follow(body as ImportJob, signal);
    } catch (err: unknown) {
      if (isAbortError(err)) return;
      setNotice({ text: err instanceof Error ? err.message : i18n.t("common:networkError"), ok: false });
    } finally {
      setBusy(false);
      setProgress(null);
    }
  }

  async function remove(name: string) {
    setConfirming(null);
    setBusy(true);
    setNotice(null);
    try {
      const res = await apiFetch(`/api/v1/backups/${encodeURIComponent(name)}`, { method: "DELETE" });
      if (!res.ok) {
        setNotice({ text: await readDetail(res), ok: false });
      } else {
        setNotice({ text: i18n.t("backup:deleted"), ok: true });
      }
      await loadList();
    } catch (err: unknown) {
      if (isAbortError(err)) return;
      setNotice({ text: err instanceof Error ? err.message : i18n.t("common:networkError"), ok: false });
    } finally {
      setBusy(false);
    }
  }

  const newest = items && items.length > 0 ? items[0].name : SAMPLE_NAME;
  const archivePath = folder ? `${folder}${folder.includes("\\") ? "\\" : "/"}${newest}` : newest;

  return (
    <section className="card backup">
      <div className="gallery-head">
        <h2>{t("backup:title")}</h2>
      </div>
      <div className="import">
        <p className="detail">{t("backup:intro")}</p>
        {folder && <p className="detail">{t("backup:folder", { folder })}</p>}
        <label className="check-row">
          <input
            type="checkbox"
            checked={withThumbs}
            disabled={busy}
            onChange={(event) => setWithThumbs(event.target.checked)}
          />
          {t("backup:withThumbs")}
        </label>
        <div className="button-row">
          <button type="button" disabled={busy} onClick={() => void start()}>
            {busy ? t("common:working") : t("backup:start")}
          </button>
        </div>
        {progress && <p className="detail import-progress">{progress}</p>}
        {notice && <p className={notice.ok ? "detail import-progress" : "status fail"}>{notice.text}</p>}
      </div>

      <h2 className="backup-heading">{t("backup:list.title")}</h2>
      {items === null && <p className="detail">{t("backup:list.loading")}</p>}
      {items && items.length === 0 && <p className="detail">{t("backup:list.empty")}</p>}
      {items && items.length > 0 && (
        <ul className="backup-list">
          {items.map((item) => (
            <li key={item.name}>
              <div className="backup-meta">
                <span>{item.name}</span>
                <p className="detail">
                  {[
                    formatDateTime(item.created_at),
                    formatBytes(item.size),
                    t("backup:users", { count: item.users }),
                    t("backup:assets", { count: item.assets }),
                    t("backup:albums", { count: item.albums }),
                    item.include_thumbs ? t("backup:thumbsIncluded") : null,
                    item.warnings.length > 0 ? t("backup:warnings", { count: item.warnings.length }) : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
              </div>
              <div className="import backup-actions">
                {confirming === item.name ? (
                  <>
                    <span className="detail">{t("backup:confirmDelete")}</span>
                    <button type="button" disabled={busy} onClick={() => void remove(item.name)}>
                      {t("backup:confirmYes")}
                    </button>
                    <button type="button" onClick={() => setConfirming(null)}>
                      {t("common:cancel")}
                    </button>
                  </>
                ) : (
                  <>
                    <a className="button-link" href={apiMediaUrl(`/api/v1/backups/${encodeURIComponent(item.name)}`)} download>
                      {t("backup:download")}
                    </a>
                    <button type="button" disabled={busy} onClick={() => setConfirming(item.name)}>
                      {t("backup:delete")}
                    </button>
                  </>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      <h2 className="backup-heading">{t("backup:restore.title")}</h2>
      <p className="detail">{t("backup:restore.help")}</p>
      <pre className="backup-command">{`python -m mosaicwave.restore "${archivePath}"`}</pre>
      <p className="detail">{t("backup:restore.check")}</p>
      <p className="detail">{t("backup:restore.keep")}</p>
    </section>
  );
}
