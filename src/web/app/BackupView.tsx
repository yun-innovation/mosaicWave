"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { apiFetch, apiMediaUrl, errorText, HostSettings, readDetail } from "./api";
import { formatBytes, formatDateTime } from "./format";
import i18n from "./i18n";
import { BackupItem, isAbortError, Notice } from "./types";
import type { TakeoutImport } from "./useTakeoutImport";

const SAMPLE_NAME = "mosaicwave-site-YYYYMMDD-HHMMSS.zip";

/**
 * Admin: site backup (start, list, download, delete). Progress and the result of a running backup
 * are in the status bar; this page reloads its list when one finishes. Restore is offline, so the
 * page shows the command.
 */
export function BackupView({ importer }: { importer: TakeoutImport }) {
  const { t } = useTranslation(["backup", "common"]);
  const [items, setItems] = useState<BackupItem[] | null>(null);
  const [folder, setFolder] = useState<string | null>(null);
  const [withThumbs, setWithThumbs] = useState(false);
  const [working, setWorking] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const { busy, finished, refreshJobs } = importer;
  const disabled = busy || working;
  const lastBackup = finished?.kind === "site_backup" ? finished.id : null;

  const loadList = useCallback(async (signal?: AbortSignal) => {
    try {
      const res = await apiFetch("/api/v1/backups", { signal });
      if (!res.ok) {
        setItems([]);
        setNotice({ text: await readDetail(res), ok: false });
        return;
      }
      setItems((await res.json()) as BackupItem[]);
    } catch (err: unknown) {
      if (isAbortError(err)) return;
      setItems((prev) => prev ?? []);
      setNotice({ text: err instanceof Error ? err.message : i18n.t("common:networkError"), ok: false });
    }
  }, []);

  // On open, and again each time a backup finishes.
  useEffect(() => {
    const ctrl = new AbortController();
    void loadList(ctrl.signal);
    return () => ctrl.abort();
  }, [loadList, lastBackup]);

  useEffect(() => {
    const ctrl = new AbortController();
    apiFetch("/api/v1/settings", { signal: ctrl.signal })
      .then(async (res) => {
        if (res.ok) setFolder(((await res.json()) as HostSettings).backup_dir);
      })
      .catch(() => undefined);
    return () => ctrl.abort();
  }, []);

  async function start() {
    setWorking(true);
    setNotice(null);
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
      refreshJobs();
    } catch (err: unknown) {
      setNotice({ text: err instanceof Error ? err.message : i18n.t("common:networkError"), ok: false });
    } finally {
      setWorking(false);
    }
  }

  async function remove(name: string) {
    setConfirming(null);
    setWorking(true);
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
      setNotice({ text: err instanceof Error ? err.message : i18n.t("common:networkError"), ok: false });
    } finally {
      setWorking(false);
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
            disabled={disabled}
            onChange={(event) => setWithThumbs(event.target.checked)}
          />
          {t("backup:withThumbs")}
        </label>
        <div className="button-row">
          <button type="button" disabled={disabled} onClick={() => void start()}>
            {disabled ? t("common:working") : t("backup:start")}
          </button>
        </div>
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
                    <button type="button" disabled={disabled} onClick={() => void remove(item.name)}>
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
                    <button type="button" disabled={disabled} onClick={() => setConfirming(item.name)}>
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
