"use client";

import { useCallback, useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { apiFetch, apiMediaUrl, errorText, readDetail } from "./api";
import { formatBytes, formatDateTime } from "./format";
import i18n from "./i18n";
import { ExportItem, isAbortError, Notice } from "./types";
import type { TakeoutImport } from "./useTakeoutImport";

/** What to export: everything, some albums, or some photos (the API takes any one of these). */
export type ExportScope = { all?: boolean; album_ids?: string[]; asset_ids?: string[] };

type Layout = "plain" | "takeout";
// Google Takeout's own choices; empty is one zip with no limit.
const SPLITS = ["", "2", "4", "10", "50"];

function fileUrl(name: string, path: string): string {
  const rest = path.split("/").map(encodeURIComponent).join("/");
  return apiMediaUrl(`/api/v1/me/exports/${encodeURIComponent(name)}/files/${rest}`);
}

/**
 * Folder layout and zip size, then a button that starts the export job. Used for the whole library
 * (Settings) and for one album. The job's progress is in the status bar; the result is listed in Settings.
 */
export function ExportStarter({
  scope,
  startLabel,
  blocked,
  onStarted,
  onNotice,
}: {
  scope: ExportScope;
  startLabel: string;
  blocked: boolean;
  onStarted: () => void;
  onNotice: (notice: Notice | null) => void;
}) {
  const { t } = useTranslation(["backup", "common"]);
  const id = useId();
  const [layout, setLayout] = useState<Layout>("plain");
  const [split, setSplit] = useState("10");
  const [working, setWorking] = useState(false);

  async function start() {
    setWorking(true);
    onNotice(null);
    try {
      const res = await apiFetch("/api/v1/me/exports", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...scope, layout, split_gb: split ? Number(split) : null }),
      });
      if (!res.ok) {
        onNotice({ text: errorText(await res.json().catch(() => null), res.status), ok: false });
        return;
      }
      onNotice({ text: i18n.t("backup:export.started"), ok: true });
      onStarted();
    } catch (err: unknown) {
      onNotice({ text: err instanceof Error ? err.message : i18n.t("common:networkError"), ok: false });
    } finally {
      setWorking(false);
    }
  }

  return (
    <div className="export-options">
      <label htmlFor={`${id}-layout`}>{t("backup:export.layout.label")}</label>
      <select id={`${id}-layout`} value={layout} onChange={(e) => setLayout(e.target.value as Layout)}>
        <option value="plain">{t("backup:export.layout.plain")}</option>
        <option value="takeout">{t("backup:export.layout.takeout")}</option>
      </select>
      <label htmlFor={`${id}-split`}>{t("backup:export.split.label")}</label>
      <select id={`${id}-split`} value={split} onChange={(e) => setSplit(e.target.value)}>
        {SPLITS.map((value) => (
          <option key={value || "one"} value={value}>
            {value ? t("backup:export.split.gb", { count: Number(value) }) : t("backup:export.split.one")}
          </option>
        ))}
      </select>
      <p className="detail">{t("backup:export.split.help")}</p>
      <div className="button-row">
        <button type="button" disabled={blocked || working} onClick={() => void start()}>
          {blocked || working ? t("common:working") : startLabel}
        </button>
      </div>
    </div>
  );
}

/** Settings: export the whole library as ordinary files, and the exports made so far. */
export function ExportPanel({ importer }: { importer: TakeoutImport }) {
  const { t } = useTranslation(["backup", "common"]);
  const [items, setItems] = useState<ExportItem[] | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const { busy, finished, refreshJobs } = importer;
  const disabled = busy || working;
  const lastFinished = finished?.kind === "export" ? finished.id : null;

  const loadList = useCallback(async (signal?: AbortSignal) => {
    try {
      const res = await apiFetch("/api/v1/me/exports", { signal });
      if (!res.ok) {
        setItems([]);
        setNotice({ text: await readDetail(res), ok: false });
        return;
      }
      setItems((await res.json()) as ExportItem[]);
    } catch (err: unknown) {
      if (isAbortError(err)) return;
      setItems((prev) => prev ?? []);
      setNotice({ text: err instanceof Error ? err.message : i18n.t("common:networkError"), ok: false });
    }
  }, []);

  // On open, and again each time an export finishes.
  useEffect(() => {
    const ctrl = new AbortController();
    void loadList(ctrl.signal);
    return () => ctrl.abort();
  }, [loadList, lastFinished]);

  async function remove(name: string) {
    setConfirming(null);
    setWorking(true);
    setNotice(null);
    try {
      const res = await apiFetch(`/api/v1/me/exports/${encodeURIComponent(name)}`, { method: "DELETE" });
      setNotice(res.ok ? { text: i18n.t("backup:export.deleted"), ok: true } : { text: await readDetail(res), ok: false });
      await loadList();
    } catch (err: unknown) {
      setNotice({ text: err instanceof Error ? err.message : i18n.t("common:networkError"), ok: false });
    } finally {
      setWorking(false);
    }
  }

  return (
    <div className="import library-export">
      <label>{t("backup:export.title")}</label>
      <p className="detail">{t("backup:export.intro")}</p>
      <ExportStarter
        scope={{ all: true }}
        startLabel={t("backup:export.all")}
        blocked={disabled}
        onNotice={setNotice}
        onStarted={refreshJobs}
      />
      {notice && <p className={notice.ok ? "detail import-progress" : "status fail"}>{notice.text}</p>}

      <h3 className="backup-heading">{t("backup:export.list.title")}</h3>
      {items === null && <p className="detail">{t("backup:list.loading")}</p>}
      {items && items.length === 0 && <p className="detail">{t("backup:export.list.empty")}</p>}
      {items && items.length > 0 && (
        <ul className="backup-list">
          {items.map((item) => {
            const loose = item.files.filter((f) => !f.zipped).length;
            return (
              <li key={item.name}>
                <div className="backup-meta">
                  <span>{item.name}</span>
                  <p className="detail">
                    {[
                      formatDateTime(item.created_at),
                      t(`backup:export.layoutName.${item.layout}`),
                      formatBytes(item.size),
                      t("backup:assets", { count: item.assets }),
                      item.albums > 0 ? t("backup:albums", { count: item.albums }) : null,
                      t("backup:export.zips", { count: item.files.length - loose }),
                      loose > 0 ? t("backup:export.unzipped", { count: loose }) : null,
                      item.warnings.length > 0 ? item.warnings.join(" ") : null,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </p>
                  <details className="export-files">
                    <summary>{t("backup:export.files")}</summary>
                    <ul className="backup-list">
                      {item.files.map((file) => (
                        <li key={file.path}>
                          <div className="backup-meta">
                            <span>{file.path}</span>
                            <p className="detail">
                              {[formatBytes(file.size), file.zipped ? null : t("backup:export.notZipped")]
                                .filter(Boolean)
                                .join(" · ")}
                            </p>
                          </div>
                          <a className="button-link" href={fileUrl(item.name, file.path)} download>
                            {t("backup:download")}
                          </a>
                        </li>
                      ))}
                    </ul>
                  </details>
                </div>
                <div className="import backup-actions">
                  {confirming === item.name ? (
                    <>
                      <span className="detail">{t("backup:export.confirmDelete")}</span>
                      <button type="button" disabled={disabled} onClick={() => void remove(item.name)}>
                        {t("backup:confirmYes")}
                      </button>
                      <button type="button" onClick={() => setConfirming(null)}>
                        {t("common:cancel")}
                      </button>
                    </>
                  ) : (
                    <button type="button" disabled={disabled} onClick={() => setConfirming(item.name)}>
                      {t("backup:delete")}
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
