"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { apiFetch, apiMediaUrl, errorText, HostSettings, readDetail } from "./api";
import { formatBytes, formatDateTime } from "./format";
import { routeHash } from "./hashRoute";
import i18n from "./i18n";
import { RestoreSourceForm } from "./RestoreSourceForm";
import { BackupItem, isAbortError, MaintenanceStatus, Notice, RestoreState, RestoreSummary } from "./types";
import type { TakeoutImport } from "./useTakeoutImport";

const SAMPLE_NAME = "mosaicwave-site-YYYYMMDD-HHMMSS.zip";

const HEALTH_MS = 1000;
// The old process exits about a second after Apply. A health answer before this, with no failure
// seen in between, can still be the old process.
const OLD_PROCESS_MS = 8000;
const GIVE_UP_MS = 180000;

type Confirm = { name: string; kind: "delete" | "restore" };
/** `wait`: restart asked, polling. `slow`: it has not come back. `manual`: nothing will restart it. */
type Waiting = "wait" | "slow" | "manual" | null;

function summaryText(summary: RestoreSummary): string {
  return [
    i18n.t("backup:users", { count: summary.users }),
    i18n.t("backup:assets", { count: summary.assets }),
    i18n.t("backup:albums", { count: summary.albums }),
  ].join(" · ");
}

/**
 * Admin: site backup (start, list, download, delete) and restore. Progress and the result of a
 * running backup or restore are in the status bar; this page reloads when one finishes.
 *
 * Restore has three steps. Restore… (needs maintenance mode) unpacks and checks an archive next to
 * the live data as a job. Restart and apply then restarts the server, which swaps the data in before
 * anything opens; this page waits for the server and reloads, and shows what happened.
 */
export function BackupView({ importer, maintenance }: { importer: TakeoutImport; maintenance: MaintenanceStatus }) {
  const { t } = useTranslation(["backup", "common"]);
  const [items, setItems] = useState<BackupItem[] | null>(null);
  const [folder, setFolder] = useState<string | null>(null);
  const [withThumbs, setWithThumbs] = useState(false);
  const [working, setWorking] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [restoreNotice, setRestoreNotice] = useState<Notice | null>(null);
  const [confirming, setConfirming] = useState<Confirm | null>(null);
  const [restore, setRestore] = useState<RestoreState | null>(null);
  const [waiting, setWaiting] = useState<Waiting>(null);
  const [applying, setApplying] = useState(false);
  const { busy, finished, refreshJobs } = importer;
  const disabled = busy || working || waiting !== null;
  const maintenanceOn = maintenance.state === "on";
  const lastFinished = finished?.kind === "site_backup" || finished?.kind === "site_restore" ? finished.id : null;

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

  const loadRestore = useCallback(async (signal?: AbortSignal) => {
    try {
      const res = await apiFetch("/api/v1/backups/restore", { signal });
      if (res.ok) setRestore((await res.json()) as RestoreState);
    } catch (err: unknown) {
      if (isAbortError(err)) return;
    }
  }, []);

  // On open, and again each time a backup or restore finishes.
  useEffect(() => {
    const ctrl = new AbortController();
    void loadList(ctrl.signal);
    void loadRestore(ctrl.signal);
    return () => ctrl.abort();
  }, [loadList, loadRestore, lastFinished]);

  // After Apply: wait for the server to go away and come back, then reload to show the outcome.
  const polling = waiting === "wait" || waiting === "slow";
  useEffect(() => {
    if (!polling) return;
    const started = Date.now();
    let sawDown = false;
    const timer = window.setInterval(() => {
      const elapsed = Date.now() - started;
      // A server that accepts the connection but never answers counts as down.
      void apiFetch("/api/v1/health", { signal: AbortSignal.timeout(3000) })
        .then((res) => {
          if (!res.ok) sawDown = true;
          else if (sawDown || elapsed > OLD_PROCESS_MS) window.location.reload();
        })
        .catch(() => {
          sawDown = true;
        });
      if (elapsed > GIVE_UP_MS) setWaiting((w) => (w === "wait" ? "slow" : w));
    }, HEALTH_MS);
    return () => window.clearInterval(timer);
  }, [polling]);

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

  async function startRestore(name: string) {
    setConfirming(null);
    setWorking(true);
    setNotice(null);
    try {
      const res = await apiFetch(`/api/v1/backups/${encodeURIComponent(name)}/restore`, { method: "POST" });
      if (!res.ok) {
        const body: unknown = await res.json().catch(() => null);
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

  async function applyRestore() {
    setApplying(true);
    setNotice(null);
    try {
      const res = await apiFetch("/api/v1/backups/restore/apply", { method: "POST" });
      const body: unknown = await res.json().catch(() => null);
      if (!res.ok) {
        setNotice({ text: errorText(body, res.status), ok: false });
        return;
      }
      setWaiting((body as { restarting?: boolean } | null)?.restarting ? "wait" : "manual");
    } catch (err: unknown) {
      setNotice({ text: err instanceof Error ? err.message : i18n.t("common:networkError"), ok: false });
    } finally {
      setApplying(false);
    }
  }

  /** Discard a staged restore, or dismiss the last result. */
  async function clearRestore() {
    setNotice(null);
    try {
      const res = await apiFetch("/api/v1/backups/restore", { method: "DELETE" });
      if (res.ok) setRestore((await res.json()) as RestoreState);
      else setNotice({ text: await readDetail(res), ok: false });
    } catch (err: unknown) {
      setNotice({ text: err instanceof Error ? err.message : i18n.t("common:networkError"), ok: false });
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
    <section className="card backup titled-sections">
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

      <h3 className="section-title divided">{t("backup:list.title")}</h3>
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
                {confirming?.name === item.name ? (
                  <>
                    <span className="detail">
                      {confirming.kind === "restore" ? t("backup:restore.confirm") : t("backup:confirmDelete")}
                    </span>
                    <button
                      type="button"
                      disabled={disabled}
                      onClick={() => void (confirming.kind === "restore" ? startRestore(item.name) : remove(item.name))}
                    >
                      {confirming.kind === "restore" ? t("backup:restore.confirmYes") : t("backup:confirmYes")}
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
                    <button
                      type="button"
                      disabled={disabled || !maintenanceOn || !!restore?.pending}
                      title={maintenanceOn ? undefined : t("backup:restore.needsMaintenance")}
                      onClick={() => setConfirming({ name: item.name, kind: "restore" })}
                    >
                      {t("backup:restore.button")}
                    </button>
                    <button type="button" disabled={disabled} onClick={() => setConfirming({ name: item.name, kind: "delete" })}>
                      {t("backup:delete")}
                    </button>
                  </>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      <h3 className="section-title divided">{t("backup:restore.title")}</h3>
      {!maintenanceOn && !waiting && (
        <p className="detail">
          {t("backup:restore.needsMaintenance")}{" "}
          <a href={routeHash({ view: "maintenance", albumId: null })}>{t("backup:restore.turnOn")}</a>
        </p>
      )}
      {waiting && (
        <p className={waiting === "wait" ? "detail import-progress" : "status fail"}>
          {waiting === "wait" && t("backup:restore.restarting.text")}
          {waiting === "slow" && t("backup:restore.restarting.slow")}
          {waiting === "manual" && t("backup:restore.restarting.manual")}
        </p>
      )}
      {restore?.result && (
        <div className="restore-box">
          <p className={restore.result.status === "done" ? "detail import-progress" : "status fail"}>
            {restore.result.status === "done"
              ? t("backup:restore.result.done", {
                  archive: restore.result.archive,
                  summary: summaryText(restore.result.summary),
                })
              : t("backup:restore.result.failed", {
                  archive: restore.result.archive,
                  message: restore.result.message ?? "",
                })}
          </p>
          {restore.result.status === "done" && (
            <>
              {restore.result.kept.length > 0 && (
                <>
                  <p className="detail">{t("backup:restore.result.kept")}</p>
                  <ul className="restore-kept">
                    {restore.result.kept.map((path) => (
                      <li key={path}>
                        <code>{path}</code>
                      </li>
                    ))}
                  </ul>
                </>
              )}
              {restore.result.upgrade && <p className="detail">{t("backup:restore.result.upgrade")}</p>}
              {restore.result.thumbs_missing && <p className="detail">{t("backup:restore.result.thumbs")}</p>}
              <p className="detail">{t("backup:restore.result.users")}</p>
              {maintenanceOn && <p className="detail">{t("backup:restore.result.maintenance")}</p>}
            </>
          )}
          <div className="button-row">
            <button type="button" onClick={() => void clearRestore()}>
              {t("backup:restore.result.dismiss")}
            </button>
          </div>
        </div>
      )}
      {restore?.pending && !waiting && (
        <div className="restore-box">
          <h3>{t("backup:restore.pending.title")}</h3>
          <p>
            {t("backup:restore.pending.summary", {
              archive: restore.pending.archive,
              summary: summaryText(restore.pending.summary),
            })}
          </p>
          <p className="detail">{t("backup:restore.pending.warn")}</p>
          {restore.pending.upgrade && <p className="detail">{t("backup:restore.pending.upgrade")}</p>}
          <div className="button-row">
            <button type="button" disabled={disabled || applying || !maintenanceOn} onClick={() => void applyRestore()}>
              {applying ? t("common:working") : t("backup:restore.pending.apply")}
            </button>
            <button type="button" disabled={disabled || applying} onClick={() => void clearRestore()}>
              {t("backup:restore.pending.discard")}
            </button>
          </div>
        </div>
      )}
      {!restore?.pending && !waiting && (
        <>
          <p className="detail">{t("backup:restore.idle")}</p>
          <RestoreSourceForm
            maintenanceOn={maintenanceOn}
            blocked={disabled}
            onNotice={setRestoreNotice}
            onStarted={() => {
              refreshJobs();
              void loadList();
              void loadRestore();
            }}
          />
          {restoreNotice && (
            <p className={restoreNotice.ok ? "detail import-progress" : "status fail"}>{restoreNotice.text}</p>
          )}
        </>
      )}

      <h3 className="section-title divided">{t("backup:restore.offline.title")}</h3>
      <p className="detail">{t("backup:restore.offline.help")}</p>
      <pre className="backup-command">{`python -m mosaicwave.restore "${archivePath}"`}</pre>
      <p className="detail">{t("backup:restore.offline.check")}</p>
      <p className="detail">{t("backup:restore.offline.keep")}</p>
    </section>
  );
}
