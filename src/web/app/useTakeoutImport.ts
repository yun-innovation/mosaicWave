"use client";

import { useCallback, useEffect, useState } from "react";

import { apiFetch, errorText, Me, readDetail } from "./api";
import i18n from "./i18n";
import { ImportJob, isAbortError, TakeoutInspect } from "./types";
import { useActiveJob } from "./useActiveJob";

function clientDumpPath(file: File): string {
  const raw = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
  const parts = raw.replace(/\\/g, "/").split("/").filter(Boolean);
  if (parts.length > 1) return parts.slice(1).join("/");
  return parts[0] || file.name;
}

export type TakeoutFrom = "server" | "device";

export type TakeoutImport = ReturnType<typeof useTakeoutImport>;

/**
 * Takeout import form state and the browser-folder push, plus the signed-in user's job tracker
 * (`useActiveJob`). Lives in the app shell, not in a view, so a running job keeps being followed
 * while the user browses other views. `onFinished` runs after a job ends or a push is cancelled.
 *
 * `message` is the Import form's own result (folder check, upload steps, import result). Job
 * progress is not in it: the status bar shows that for every job kind.
 */
export function useTakeoutImport(me: Me | null, onFinished: () => void) {
  const [path, setPath] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [inspect, setInspect] = useState<TakeoutInspect | null>(null);
  const [working, setWorking] = useState(false);
  const [from, setFrom] = useState<TakeoutFrom>("server");
  const [clientFiles, setClientFiles] = useState<File[]>([]);
  const [pushJobId, setPushJobId] = useState<string | null>(null);

  const applyJobResult = useCallback(
    (job: ImportJob) => {
      if (job.status === "error") {
        if (job.kind === "takeout_import") setMessage(job.error || i18n.t("import:fail.import"));
        onFinished();
        return;
      }
      if (job.status === "cancelled") {
        if (job.kind === "takeout_import") setMessage(i18n.t("import:result.importCancelled"));
        onFinished(); // finished items stay in the library
        return;
      }
      if (job.status !== "done") return;
      if (job.kind === "takeout_import") {
        const counts = {
          assets: i18n.t("import:result.assets", { count: job.assets }),
          albums: i18n.t("import:result.albums", { count: job.albums }),
        };
        setMessage(
          job.import_root
            ? i18n.t("import:result.importedFrom", { ...counts, path: job.import_root })
            : i18n.t("import:result.imported", counts),
        );
        if (job.warnings.length > 0) {
          setInspect((prev) =>
            prev
              ? { ...prev, warnings: job.warnings }
              : {
                  kind: "dump",
                  extract_root: null,
                  photos_root: job.import_root,
                  import_root: job.import_root,
                  zip_count: 0,
                  sidecar_without_media_count: 0,
                  sidecar_without_media: [],
                  album_metadata_count: 0,
                  ready: true,
                  warnings: job.warnings,
                  loose_media: [],
                },
          );
        }
      }
      onFinished();
    },
    [onFinished],
  );

  const tracker = useActiveJob(me, applyJobResult);
  const { refresh } = tracker;

  // A browser-folder push that was still receiving files when the page reloaded.
  useEffect(() => {
    if (!me) return;
    const ctrl = new AbortController();
    apiFetch("/api/v1/import/takeout/status", { signal: ctrl.signal })
      .then(async (res) => {
        if (!res.ok) return;
        const job = (await res.json()) as ImportJob;
        if (job.status !== "running" || job.mode !== "push" || job.phase !== "receiving") return;
        setPushJobId(job.id);
        setMessage(
          i18n.t("import:progress.waitingDevice", {
            progress: job.total ? `${job.processed} / ${job.total}` : String(job.processed),
          }),
        );
      })
      .catch((err: unknown) => {
        if (isAbortError(err)) return;
      });
    return () => ctrl.abort();
  }, [me]);

  async function postTakeout(inspectOnly: boolean) {
    const url = inspectOnly ? "/api/v1/import/takeout/inspect" : "/api/v1/import/takeout";
    const res = await apiFetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: path || null }),
    });
    const body: unknown = await res.json().catch(() => null);
    return { res, body };
  }

  async function check() {
    setWorking(true);
    setMessage(null);
    try {
      const { res, body } = await postTakeout(true);
      if (!res.ok) {
        setInspect(null);
        setMessage(errorText(body, res.status));
        return;
      }
      setInspect(body as TakeoutInspect);
    } catch (err: unknown) {
      setInspect(null);
      setMessage(err instanceof Error ? err.message : i18n.t("import:fail.check"));
    } finally {
      setWorking(false);
    }
  }

  async function attachReceivingPush() {
    const res = await apiFetch("/api/v1/import/takeout/status");
    if (!res.ok) return;
    const job = (await res.json()) as ImportJob;
    if (job.status !== "running" || job.mode !== "push" || job.phase !== "receiving" || !job.id) {
      return;
    }
    setPushJobId(job.id);
  }

  async function cancelPush() {
    if (!pushJobId) return;
    setWorking(true);
    try {
      const res = await apiFetch(`/api/v1/import/takeout/push/${pushJobId}/cancel`, { method: "POST" });
      if (!res.ok) {
        setMessage(await readDetail(res));
        return;
      }
      setPushJobId(null);
      setMessage(i18n.t("import:result.cancelled"));
      refresh();
      onFinished();
    } finally {
      setWorking(false);
    }
  }

  /** A job that came back from a start/finish call: follow it, or apply it if it already ended. */
  function followOrApply(job: ImportJob) {
    if (job.status === "running" || job.status === "queued") {
      refresh();
      return;
    }
    applyJobResult(job);
  }

  async function deviceImport() {
    if (clientFiles.length === 0) {
      setMessage(i18n.t("import:fail.chooseFolder"));
      return;
    }
    setWorking(true);
    setMessage(null);
    try {
      const startRes = await apiFetch("/api/v1/import/takeout/push", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_count: clientFiles.length }),
      });
      if (!startRes.ok) {
        setMessage(await readDetail(startRes));
        if (startRes.status === 409) {
          await attachReceivingPush();
        }
        return;
      }
      const started = (await startRes.json()) as ImportJob;
      if (!started.id) {
        setMessage(i18n.t("import:fail.noJobId"));
        return;
      }
      setPushJobId(started.id);
      for (let i = 0; i < clientFiles.length; i += 1) {
        const file = clientFiles[i];
        const rel = clientDumpPath(file);
        setMessage(
          i18n.t("import:progress.uploading", { index: i + 1, total: clientFiles.length, path: rel }),
        );
        const put = await apiFetch(
          `/api/v1/import/takeout/push/${started.id}/file?relative_path=${encodeURIComponent(rel)}`,
          {
            method: "POST",
            headers: { "Content-Type": "application/octet-stream" },
            body: file,
          },
        );
        if (!put.ok) {
          setMessage(await readDetail(put));
          return;
        }
      }
      setMessage(i18n.t("import:progress.uploadComplete"));
      const fin = await apiFetch(`/api/v1/import/takeout/push/${started.id}/finish`, {
        method: "POST",
      });
      if (!fin.ok) {
        setMessage(await readDetail(fin));
        return;
      }
      setPushJobId(null);
      followOrApply((await fin.json()) as ImportJob);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : i18n.t("import:fail.import");
      setMessage(msg === "Failed to fetch" ? i18n.t("import:fail.connectionDropped") : msg);
    } finally {
      setWorking(false);
    }
  }

  async function startImport() {
    if (from === "device") {
      await deviceImport();
      return;
    }
    setWorking(true);
    setMessage(null);
    try {
      const { res, body } = await postTakeout(false);
      if (!res.ok) {
        setMessage(errorText(body, res.status));
        return;
      }
      followOrApply(body as ImportJob);
    } catch (err: unknown) {
      setMessage(err instanceof Error ? err.message : i18n.t("import:fail.import"));
    } finally {
      setWorking(false);
    }
  }

  /** Starts a thumbnail job. Returns an error text, or null when the job started. */
  async function generateThumbs(): Promise<string | null> {
    setWorking(true);
    try {
      const res = await apiFetch("/api/v1/thumbs/generate", { method: "POST" });
      const body: unknown = await res.json().catch(() => null);
      if (!res.ok) return errorText(body, res.status);
      followOrApply(body as ImportJob);
      return null;
    } catch (err: unknown) {
      return err instanceof Error ? err.message : i18n.t("import:fail.thumbs");
    } finally {
      setWorking(false);
    }
  }

  return {
    path,
    setPath,
    message,
    inspect,
    /** True while this tab is starting something or the server is running any job. */
    busy: working || tracker.job !== null,
    /** True only while this tab itself is starting or uploading something. */
    working,
    job: tracker.job,
    finished: tracker.finished,
    dismissFinished: tracker.dismissFinished,
    refreshJobs: refresh,
    from,
    setFrom,
    clientFiles,
    setClientFiles,
    pushJobId,
    check,
    startImport,
    cancelPush,
    generateThumbs,
  };
}
