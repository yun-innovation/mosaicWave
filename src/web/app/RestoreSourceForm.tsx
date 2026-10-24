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
export function RestoreSourceForm({ maintenanceOn, blocked, onStarted, onNotice }: Props) {
  const { t } = useTranslation(["backup", "common"]);
  const [from, setFrom] = useState<From>("server");
  const [path, setPath] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [working, setWorking] = useState(false);
  const [sent, setSent] = useState<{ sent: number; total: number } | null>(null);
  const upload = useRef<BackupUpload | null>(null);

  const idle = !blocked && !working;
  const ready = maintenanceOn && idle && (from === "server" ? path.trim() !== "" : file !== null);

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
    const failure = await postRestore("/api/v1/backups/restore", { path: path.trim() });
    if (failure) onNotice({ text: failure, ok: false });
    else onStarted();
  }

  async function fromDevice(chosen: File) {
    const sending = uploadBackup(chosen, (done, total) => setSent({ sent: done, total }));
    upload.current = sending;
    setSent({ sent: 0, total: chosen.size });
    let name: string;
    try {
      name = (await sending.done).name;
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
    const failure = await postRestore(`/api/v1/backups/${encodeURIComponent(name)}/restore`);
    if (failure) {
      onNotice({ text: i18n.t("backup:restore.source.uploadedKept", { message: failure }), ok: false });
    }
    onStarted();
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!ready) return;
    setWorking(true);
    onNotice(null);
    try {
      if (from === "server") await fromServer();
      else if (file) await fromDevice(file);
    } catch (err: unknown) {
      onNotice({ text: err instanceof Error ? err.message : i18n.t("common:networkError"), ok: false });
    } finally {
      setWorking(false);
    }
  }

  const percent = sent && sent.total > 0 ? Math.min(100, Math.floor((sent.sent / sent.total) * 100)) : 0;

  return (
    <>
      <form className="import restore-source" onSubmit={(e) => void onSubmit(e)}>
        <div className="choice-col">
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
                placeholder={t("backup:restore.source.pathPlaceholder")}
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
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              disabled={!idle}
            />
            <p className="detail">
              {file
                ? t("backup:restore.source.deviceSelected", { name: file.name, size: formatBytes(file.size) })
                : t("backup:restore.source.deviceHint")}
            </p>
          </>
        )}
        {sent && (
          <p className="detail import-progress">
            <progress className="status-bar-meter" max={sent.total} value={sent.sent} />{" "}
            {t("backup:restore.source.uploading", { percent })}
          </p>
        )}
        <div className="button-row">
          <button type="submit" disabled={!ready}>
            {working
              ? t("common:working")
              : from === "server"
                ? t("backup:restore.source.restore")
                : t("backup:restore.source.upload")}
          </button>
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
