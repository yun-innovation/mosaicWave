"use client";

import { useCallback, useEffect, useState } from "react";

import { apiFetch, errorText, Me, readDetail } from "./api";
import i18n from "./i18n";
import { ImportJob, isAbortError, TakeoutInspect } from "./types";

function clientDumpPath(file: File): string {
  const raw = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
  const parts = raw.replace(/\\/g, "/").split("/").filter(Boolean);
  if (parts.length > 1) return parts.slice(1).join("/");
  return parts[0] || file.name;
}

export type TakeoutFrom = "server" | "device";

export type TakeoutImport = ReturnType<typeof useTakeoutImport>;

/**
 * Takeout import and thumbnail jobs. Lives in the app shell, not in a view, so a running job keeps
 * polling while the user browses other views. `onFinished` runs after a job ends or a push is cancelled.
 */
export function useTakeoutImport(me: Me | null, onFinished: () => void) {
  const [path, setPath] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [inspect, setInspect] = useState<TakeoutInspect | null>(null);
  const [busy, setBusy] = useState(false);
  const [from, setFrom] = useState<TakeoutFrom>("server");
  const [clientFiles, setClientFiles] = useState<File[]>([]);
  const [pushJobId, setPushJobId] = useState<string | null>(null);

  const applyJobResult = useCallback(
    (job: ImportJob) => {
      if (job.status === "error") {
        setMessage(job.error || i18n.t("import:fail.import"));
        onFinished();
        return;
      }
      if (job.status !== "done") return;
      if (job.kind === "thumb_batch") {
        setMessage(i18n.t("import:result.thumbsReady", { count: job.processed }));
      } else {
        const counts = {
          assets: i18n.t("import:result.assets", { count: job.assets }),
          albums: i18n.t("import:result.albums", { count: job.albums }),
        };
        setMessage(
          job.import_root
            ? i18n.t("import:result.importedFrom", { ...counts, path: job.import_root })
            : i18n.t("import:result.imported", counts),
        );
      }
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
      onFinished();
    },
    [onFinished],
  );

  const poll = useCallback(
    async (signal?: AbortSignal, jobId?: string | null) => {
      try {
        for (;;) {
          const url = jobId ? `/api/v1/jobs/${jobId}` : "/api/v1/import/takeout/status";
          const res = await apiFetch(url, { signal });
          if (!res.ok) {
            setMessage(await readDetail(res));
            return;
          }
          const job = (await res.json()) as ImportJob;
          if (job.status === "running") {
            const values = { processed: job.processed, total: job.total || "?" };
            const label =
              job.kind === "thumb_batch"
                ? i18n.t("import:progress.thumbs", values)
                : i18n.t("import:progress.importing", values);
            setMessage(job.current ? `${label} · ${job.current}` : label);
            await new Promise((r) => setTimeout(r, 500));
            continue;
          }
          applyJobResult(job);
          return;
        }
      } catch (err: unknown) {
        if (isAbortError(err)) return;
        throw err;
      }
    },
    [applyJobResult],
  );

  useEffect(() => {
    if (!me) return;
    const ctrl = new AbortController();
    apiFetch("/api/v1/import/takeout/status", { signal: ctrl.signal })
      .then(async (res) => {
        if (!res.ok) return;
        const job = (await res.json()) as ImportJob;
        if (job.status !== "running") return;
        if (job.mode === "push" && job.phase === "receiving") {
          setPushJobId(job.id);
          setMessage(
            i18n.t("import:progress.waitingDevice", {
              progress: job.total ? `${job.processed} / ${job.total}` : String(job.processed),
            }),
          );
          return;
        }
        setBusy(true);
        try {
          await poll(ctrl.signal, job.id);
        } finally {
          setBusy(false);
        }
      })
      .catch((err: unknown) => {
        if (isAbortError(err)) return;
      });
    return () => ctrl.abort("unmount");
  }, [me, poll]);

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
    setBusy(true);
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
      setBusy(false);
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
    setBusy(true);
    try {
      const res = await apiFetch(`/api/v1/import/takeout/push/${pushJobId}/cancel`, { method: "POST" });
      if (!res.ok) {
        setMessage(await readDetail(res));
        return;
      }
      setPushJobId(null);
      setMessage(i18n.t("import:result.cancelled"));
      onFinished();
    } finally {
      setBusy(false);
    }
  }

  async function deviceImport() {
    if (clientFiles.length === 0) {
      setMessage(i18n.t("import:fail.chooseFolder"));
      return;
    }
    setBusy(true);
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
      const job = (await fin.json()) as ImportJob;
      if (job.status === "running") {
        await poll(undefined, job.id);
        return;
      }
      applyJobResult(job);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : i18n.t("import:fail.import");
      setMessage(msg === "Failed to fetch" ? i18n.t("import:fail.connectionDropped") : msg);
    } finally {
      setBusy(false);
    }
  }

  async function startImport() {
    if (from === "device") {
      await deviceImport();
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const { res, body } = await postTakeout(false);
      if (!res.ok) {
        setMessage(errorText(body, res.status));
        return;
      }
      const job = body as ImportJob;
      if (job.status === "running") {
        setMessage(i18n.t("import:progress.importingShort"));
        await poll(undefined, job.id);
        return;
      }
      applyJobResult(job);
    } catch (err: unknown) {
      setMessage(err instanceof Error ? err.message : i18n.t("import:fail.import"));
    } finally {
      setBusy(false);
    }
  }

  async function generateThumbs() {
    setBusy(true);
    setMessage(null);
    try {
      const res = await apiFetch("/api/v1/thumbs/generate", { method: "POST" });
      const body: unknown = await res.json().catch(() => null);
      if (!res.ok) {
        setMessage(errorText(body, res.status));
        return;
      }
      const job = body as ImportJob;
      if (job.status === "running") {
        setMessage(i18n.t("import:progress.generatingThumbs"));
        await poll(undefined, job.id);
        return;
      }
      applyJobResult(job);
    } catch (err: unknown) {
      setMessage(err instanceof Error ? err.message : i18n.t("import:fail.thumbs"));
    } finally {
      setBusy(false);
    }
  }

  return {
    path,
    setPath,
    message,
    inspect,
    busy,
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
