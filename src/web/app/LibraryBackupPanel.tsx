"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { apiFetch, apiMediaUrl, errorText, readDetail } from "./api";
import { formatBytes, formatDateTime } from "./format";
import i18n from "./i18n";
import { RestoreSourceForm } from "./RestoreSourceForm";
import { isAbortError, LibraryBackupItem, Notice } from "./types";
import type { TakeoutImport } from "./useTakeoutImport";

type Confirm = { name: string; kind: "delete" | "restore" };

/**
 * Back up and restore the signed-in user's own library (Settings, any role). A backup is a zip kept on
 * the server under this user's name; Restore… replaces this library with it while the server runs and
 * leaves everyone else alone. Progress and the result of a running job are in the status bar.
 */
export function LibraryBackupPanel({ importer, isAdmin }: { importer: TakeoutImport; isAdmin: boolean }) {
  const { t } = useTranslation(["backup", "common"]);
  const [items, setItems] = useState<LibraryBackupItem[] | null>(null);
  const [working, setWorking] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [confirming, setConfirming] = useState<Confirm | null>(null);
  const { busy, finished, refreshJobs } = importer;
  const disabled = busy || working;
  const lastFinished =
    finished?.kind === "library_backup" || finished?.kind === "library_restore" ? finished.id : null;

  const loadList = useCallback(async (signal?: AbortSignal) => {
    try {
      const res = await apiFetch("/api/v1/me/backups", { signal });
      if (!res.ok) {
        setItems([]);
        setNotice({ text: await readDetail(res), ok: false });
        return;
      }
      setItems((await res.json()) as LibraryBackupItem[]);
    } catch (err: unknown) {
      if (isAbortError(err)) return;
      setItems((prev) => prev ?? []);
      setNotice({ text: err instanceof Error ? err.message : i18n.t("common:networkError"), ok: false });
    }
  }, []);

  // On open, and again each time a library backup or restore finishes.
  useEffect(() => {
    const ctrl = new AbortController();
    void loadList(ctrl.signal);
    return () => ctrl.abort();
  }, [loadList, lastFinished]);

  async function post(url: string): Promise<boolean> {
    setWorking(true);
    setNotice(null);
    try {
      const res = await apiFetch(url, { method: "POST" });
      if (!res.ok) {
        setNotice({ text: errorText(await res.json().catch(() => null), res.status), ok: false });
        return false;
      }
      refreshJobs();
      return true;
    } catch (err: unknown) {
      setNotice({ text: err instanceof Error ? err.message : i18n.t("common:networkError"), ok: false });
      return false;
    } finally {
      setWorking(false);
    }
  }

  async function restore(name: string) {
    setConfirming(null);
    await post(`/api/v1/me/backups/${encodeURIComponent(name)}/restore`);
  }

  async function remove(name: string) {
    setConfirming(null);
    setWorking(true);
    setNotice(null);
    try {
      const res = await apiFetch(`/api/v1/me/backups/${encodeURIComponent(name)}`, { method: "DELETE" });
      setNotice(res.ok ? { text: i18n.t("backup:deleted"), ok: true } : { text: await readDetail(res), ok: false });
      await loadList();
    } catch (err: unknown) {
      setNotice({ text: err instanceof Error ? err.message : i18n.t("common:networkError"), ok: false });
    } finally {
      setWorking(false);
    }
  }

  return (
    <div className="import library-backup">
      <h3 className="section-title">{t("backup:library.title")}</h3>
      <p className="detail">{t("backup:library.intro")}</p>
      <div className="button-row">
        <button type="button" disabled={disabled} onClick={() => void post("/api/v1/me/backups")}>
          {disabled ? t("common:working") : t("backup:library.start")}
        </button>
      </div>
      {notice && <p className={notice.ok ? "detail import-progress" : "status fail"}>{notice.text}</p>}

      <h3 className="backup-heading">{t("backup:library.list.title")}</h3>
      {items === null && <p className="detail">{t("backup:list.loading")}</p>}
      {items && items.length === 0 && <p className="detail">{t("backup:library.list.empty")}</p>}
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
                    t("backup:assets", { count: item.assets }),
                    t("backup:albums", { count: item.albums }),
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
                      {confirming.kind === "restore" ? t("backup:library.restore.confirm") : t("backup:confirmDelete")}
                    </span>
                    <button
                      type="button"
                      disabled={disabled}
                      onClick={() => void (confirming.kind === "restore" ? restore(item.name) : remove(item.name))}
                    >
                      {confirming.kind === "restore" ? t("backup:library.restore.confirmYes") : t("backup:confirmYes")}
                    </button>
                    <button type="button" onClick={() => setConfirming(null)}>
                      {t("common:cancel")}
                    </button>
                  </>
                ) : (
                  <>
                    <a
                      className="button-link"
                      href={apiMediaUrl(`/api/v1/me/backups/${encodeURIComponent(item.name)}`)}
                      download
                    >
                      {t("backup:download")}
                    </a>
                    <button type="button" disabled={disabled} onClick={() => setConfirming({ name: item.name, kind: "restore" })}>
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
      <p className="detail">{t("backup:library.keepNote")}</p>

      <h3 className="backup-heading">{t("backup:library.source.title")}</h3>
      <RestoreSourceForm
        scope="library"
        serverEnabled={isAdmin}
        maintenanceOn
        blocked={disabled}
        onNotice={setNotice}
        onStarted={() => {
          refreshJobs();
          void loadList();
        }}
      />
    </div>
  );
}
