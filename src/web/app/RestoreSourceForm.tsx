"use client";

import { FormEvent, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { apiFetch, errorText } from "./api";
import { formatBytes } from "./format";
import { FolderPicker } from "./FolderPicker";
import i18n from "./i18n";
import { isAbortError, Notice } from "./types";
import { BackupUpload, uploadBackup } from "./uploadBackup";

type Props = {
  /** `site`: the admin's site restore (staged, applied at restart). `library`: replace your own library. */
  scope?: "site" | "library";
  /** Offer "On the server" (needs a server path; admins only for a library restore). */
  serverEnabled?: boolean;
  /** Restore needs maintenance mode; without it the form is shown but cannot start. */
  maintenanceOn: boolean;
  /** Something else is running (a job, a pending restart), so nothing can start. */
  blocked: boolean;
  /** Called once the server accepted the job (or an upload finished), so the page can reload. */
  onStarted: () => void;
  onNotice: (notice: Notice | null) => void;
};

type From = "server" | "device";

/**
 * Choose the backup to restore the way Import chooses a Takeout: a zip already on the server
 * (browse for it; nothing is copied) or one on this device (uploaded first, kept in the archive list).
 * Either way the result is the same staging job as Restore… on a listed archive.
 */
export function RestoreSourceForm({
  scope = "site",
  serverEnabled = true,
  maintenanceOn,
  blocked,
  onStarted,
  onNotice,
}: Props) {
  const { t } = useTranslation(["backup", "common"]);
  const library = scope === "library";
  const urls = library
    ? { path: "/api/v1/me/backups/restore", upload: "/api/v1/me/backups/upload", byName: "/api/v1/me/backups" }
    : { path: "/api/v1/backups/restore", upload: "/api/v1/backups/upload", byName: "/api/v1/backups" };
  const [from, setFrom] = useState<From>(serverEnabled ? "server" : "device");
  const [confirming, setConfirming] = useState(false);
  const [path, setPath] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [working, setWorking] = useState(false);
  const [sent, setSent] = useState<{ sent: number; total: number; index: number; count: number } | null>(null);
  const upload = useRef<BackupUpload | null>(null);

  const idle = !blocked && !working;
  const ready = maintenanceOn && idle && (from === "server" ? path.trim() !== "" : files.length > 0);

  async function postRestore(url: string, body?: unknown): Promise<string | null> {
    const res = await apiFetch(url, {
      method: "POST",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (res.ok) return null;
    return errorText(await res.json().catch(() => null), res.status);
  }

  async function fromServer() {
    const failure = await postRestore(urls.path, { path: path.trim() });
    if (failure) onNotice({ text: failure, ok: false });
    else onStarted();
  }

  /** Upload the chosen file, or every part of a split backup one after the other, then restore it. */
  async function fromDevice(chosen: File[]) {
    const total = chosen.reduce((sum, f) => sum + f.size, 0);
    let before = 0;
    let name = "";
    try {
      for (const [i, one] of chosen.entries()) {
        const sending = uploadBackup(
          one,
          (done) => setSent({ sent: before + done, total, index: i + 1, count: chosen.length }),
          urls.upload,
        );
        upload.current = sending;
        setSent({ sent: before, total, index: i + 1, count: chosen.length });
        name = (await sending.done).name;
        before += one.size;
      }
    } catch (err: unknown) {
      onNotice(
        isAbortError(err)
          ? { text: i18n.t("backup:restore.source.uploadCancelled"), ok: true }
          : { text: err instanceof Error ? err.message : i18n.t("common:networkError"), ok: false },
      );
      return;
    } finally {
      upload.current = null;
      setSent(null);
    }
    // It is in the archive list now, whatever happens next.
    const failure = await postRestore(`${urls.byName}/${encodeURIComponent(name)}/restore`);
    if (failure) {
      onNotice({ text: i18n.t("backup:restore.source.uploadedKept", { message: failure }), ok: false });
    }
    onStarted();
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!ready) return;
    // Replacing your own library cannot be undone, so ask first (a site restore is applied later).
    if (library) setConfirming(true);
    else void run();
  }

  async function run() {
    setConfirming(false);
    if (!ready) return;
    setWorking(true);
    onNotice(null);
    try {
      if (from === "server") await fromServer();
      else if (files.length > 0) await fromDevice(files);
    } catch (err: unknown) {
      onNotice({ text: err instanceof Error ? err.message : i18n.t("common:networkError"), ok: false });
    } finally {
      setWorking(false);
    }
  }

  const percent = sent && sent.total > 0 ? Math.min(100, Math.floor((sent.sent / sent.total) * 100)) : 0;

  return (
    <>
      <form className="import restore-source" onSubmit={onSubmit}>
        <div className="choice-col">
          {serverEnabled && (
            <label>
              <input
                type="radio"
                name="restore-from"
                checked={from === "server"}
                onChange={() => setFrom("server")}
                disabled={!idle}
              />
              {t("backup:restore.source.server")}
            </label>
          )}
          <label>
            <input
              type="radio"
              name="restore-from"
              checked={from === "device"}
              onChange={() => setFrom("device")}
              disabled={!idle}
            />
            {t("backup:restore.source.device")}
          </label>
        </div>
        {from === "server" ? (
          <>
            <label htmlFor="restore-path">{t("backup:restore.source.pathLabel")}</label>
            <div className="path-row">
              <input
                id="restore-path"
                value={path}
                onChange={(e) => setPath(e.target.value)}
                placeholder={t(library ? "backup:library.source.pathPlaceholder" : "backup:restore.source.pathPlaceholder")}
                autoComplete="off"
                disabled={!idle}
              />
              <button type="button" disabled={!idle} onClick={() => setPickerOpen(true)}>
                {t("common:browse")}
              </button>
            </div>
          </>
        ) : (
          <>
            <label htmlFor="restore-device">{t("backup:restore.source.deviceLabel")}</label>
            <input
              id="restore-device"
              type="file"
              accept=".zip,application/zip"
              multiple
              onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
              disabled={!idle}
            />
            <p className="detail">
              {files.length === 1
                ? t("backup:restore.source.deviceSelected", { name: files[0].name, size: formatBytes(files[0].size) })
                : files.length > 1
                  ? t("backup:restore.source.deviceSelectedMany", {
                      count: files.length,
                      size: formatBytes(files.reduce((sum, f) => sum + f.size, 0)),
                    })
                  : t(library ? "backup:library.source.deviceHint" : "backup:restore.source.deviceHint")}
            </p>
          </>
        )}
        {sent && (
          <p className="detail import-progress">
            <progress className="status-bar-meter" max={sent.total} value={sent.sent} />{" "}
            {sent.count > 1
              ? t("backup:restore.source.uploadingFile", { index: sent.index, count: sent.count, percent })
              : t("backup:restore.source.uploading", { percent })}
          </p>
        )}
        {confirming && <p className="detail">{t("backup:library.restore.confirm")}</p>}
        <div className="button-row">
          {confirming ? (
            <>
              <button type="button" onClick={() => void run()}>
                {t("backup:library.restore.confirmYes")}
              </button>
              <button type="button" onClick={() => setConfirming(false)}>
                {t("common:cancel")}
              </button>
            </>
          ) : (
            <button type="submit" disabled={!ready}>
              {working
                ? t("common:working")
                : from === "server"
                  ? t("backup:restore.source.restore")
                  : t("backup:restore.source.upload")}
            </button>
          )}
          {sent && (
            <button type="button" onClick={() => upload.current?.cancel()}>
              {t("backup:restore.source.cancelUpload")}
            </button>
          )}
        </div>
      </form>
      {pickerOpen && (
        <FolderPicker
          kind="backup"
          startPath={path.replace(/[\\/][^\\/]*$/, "")}
          title={t("backup:restore.source.pickerTitle")}
          intro={t("backup:restore.source.pickerIntro")}
          onSelect={(picked) => {
            setPath(picked);
            setPickerOpen(false);
          }}
          onClose={() => setPickerOpen(false)}
        />
      )}
    </>
  );
}
