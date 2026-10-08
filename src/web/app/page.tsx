"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { BrandLockup } from "./BrandLockup";
import { FolderPicker } from "./FolderPicker";
import { AuthScreen } from "./AuthScreen";
import { SettingsPanel } from "./SettingsPanel";
import { VideoPlayer } from "./VideoPlayer";
import { apiFetch, apiMediaUrl, assetFileUrl, AuthStatus, Me, readDetail, Source } from "./api";

type HealthState =
  | { kind: "loading" }
  | { kind: "ok" }
  | { kind: "fail"; message: string };

type Asset = {
  id: string;
  filename: string;
  taken_at: string | null;
  mime: string | null;
  size: number;
  thumb_rev: number;
};

type DumpWarning = {
  code: string;
  severity: string;
  message: string;
  path: string | null;
};

type TakeoutInspect = {
  kind: string;
  extract_root: string | null;
  photos_root: string | null;
  import_root: string | null;
  zip_count: number;
  sidecar_without_media_count: number;
  sidecar_without_media: string[];
  album_metadata_count: number;
  ready: boolean;
  warnings: DumpWarning[];
  loose_media: { name: string; size: number; part: number | null; original_name: string | null }[];
};

type AssetList = {
  items: Asset[];
  next_cursor: string | null;
};

type LibraryAlbum = {
  id: string;
  name: string;
  cover_asset_id: string | null;
  asset_count: number;
};

type ImportJob = {
  id: string | null;
  kind: string | null;
  status: "idle" | "running" | "done" | "error" | string;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  path: string | null;
  processed: number;
  total: number;
  current: string | null;
  source_id: string | null;
  assets: number;
  albums: number;
  memberships: number;
  import_root: string | null;
  mode: string | null;
  phase: string | null;
  error: string | null;
  warnings: DumpWarning[];
};

function isAbortError(err: unknown): boolean {
  return typeof err === "object" && err !== null && "name" in err && (err as { name: string }).name === "AbortError";
}

function ignoreAbort(task: Promise<unknown>): void {
  void task.catch((err: unknown) => {
    if (!isAbortError(err)) throw err;
  });
}

function clientDumpPath(file: File): string {
  const raw = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
  const parts = raw.replace(/\\/g, "/").split("/").filter(Boolean);
  if (parts.length > 1) return parts.slice(1).join("/");
  return parts[0] || file.name;
}

function parseStamp(value: string | null): Date | null {
  if (!value) return null;
  const hasTz = /Z$/i.test(value) || /[+-]\d{2}:\d{2}$/.test(value);
  const d = new Date(hasTz ? value : `${value}Z`);
  if (Number.isNaN(d.getTime())) return null;
  return d;
}

function formatDate(value: string | null): string {
  const d = parseStamp(value);
  if (!d) return value || "unknown date";
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function formatJobWhen(value: string | null): string {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toISOString().slice(0, 16).replace("T", " ");
}

function jobKindLabel(kind: string | null): string {
  if (kind === "takeout_import") return "Takeout import";
  if (kind === "thumb_batch") return "Thumbs";
  return kind || "job";
}

function isVideo(asset: Asset): boolean {
  if (asset.mime?.startsWith("video/")) return true;
  return /\.(mp4|mov|m4v|avi|mkv|webm|3gp)$/i.test(asset.filename);
}

function needsJpegPreview(asset: Asset): boolean {
  if (isVideo(asset)) return false;
  if (/\.(heic|heif|tif|tiff|dng)$/i.test(asset.filename)) return true;
  return /image\/(heic|heif|tiff|x-adobe-dng)/i.test(asset.mime || "");
}

function stillSrc(asset: Asset): string {
  if (needsJpegPreview(asset)) {
    return `/api/v1/assets/${asset.id}/preview?rev=${asset.thumb_rev}`;
  }
  return `/api/v1/assets/${asset.id}/file`;
}

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function dayKey(value: string | null): string {
  if (!value) return "Unknown date";
  const formatted = formatDate(value);
  return formatted === "unknown date" ? "Unknown date" : formatted;
}

function monthLabel(day: string): string {
  if (day === "Unknown date" || day === "unknown date") return "Unknown date";
  const d = new Date(`${day}T12:00:00`);
  if (Number.isNaN(d.getTime())) return day;
  return d.toLocaleString(undefined, { month: "long", year: "numeric" });
}

type DayGroup = { day: string; items: Asset[] };

function groupByDay(assets: Asset[]): DayGroup[] {
  const map = new Map<string, Asset[]>();
  const order: string[] = [];
  for (const asset of assets) {
    const key = dayKey(asset.taken_at);
    if (!map.has(key)) {
      map.set(key, []);
      order.push(key);
    }
    map.get(key)!.push(asset);
  }
  return order.map((day) => ({ day, items: map.get(day)! }));
}

type TimelineBlock = { month: string | null; day: string; items: Asset[] };

function timelineBlocks(assets: Asset[]): TimelineBlock[] {
  let lastMonth = "";
  return groupByDay(assets).map((group) => {
    const month = monthLabel(group.day);
    const heading = month !== lastMonth ? month : null;
    lastMonth = month;
    return { month: heading, day: group.day, items: group.items };
  });
}

export default function Home() {
  const [health, setHealth] = useState<HealthState>({ kind: "loading" });
  const [me, setMe] = useState<Me | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [needsSetup, setNeedsSetup] = useState(false);
  const [platform, setPlatform] = useState("standalone");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [writeSources, setWriteSources] = useState<Source[]>([]);
  const [uploadSource, setUploadSource] = useState("");
  const [assets, setAssets] = useState<Asset[] | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [importPath, setImportPath] = useState("");
  const [importMessage, setImportMessage] = useState<string | null>(null);
  const [inspect, setInspect] = useState<TakeoutInspect | null>(null);
  const [busy, setBusy] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [takeoutFrom, setTakeoutFrom] = useState<"server" | "device">("server");
  const [clientFiles, setClientFiles] = useState<File[]>([]);
  const [pushJobId, setPushJobId] = useState<string | null>(null);
  const [jobs, setJobs] = useState<ImportJob[] | null>(null);
  const [selected, setSelected] = useState<Asset | null>(null);
  const [galleryTab, setGalleryTab] = useState<"timeline" | "albums">("timeline");
  const [openAlbum, setOpenAlbum] = useState<LibraryAlbum | null>(null);
  const [albums, setAlbums] = useState<LibraryAlbum[] | null>(null);
  const [newAlbumName, setNewAlbumName] = useState("");
  const [addAlbumId, setAddAlbumId] = useState("");
  const [albumMessage, setAlbumMessage] = useState<string | null>(null);

  const albumId = openAlbum?.id ?? null;

  const loadAssets = useCallback(async (signal?: AbortSignal, cursor?: string | null) => {
    if (!cursor) setListError(null);
    try {
      const qs = new URLSearchParams({ limit: "50" });
      if (cursor) qs.set("cursor", cursor);
      if (albumId) qs.set("album_id", albumId);
      const res = await apiFetch(`/api/v1/assets?${qs}`, { signal });
      if (!res.ok) {
        if (!cursor) setAssets(null);
        setListError(`HTTP ${res.status}`);
        return;
      }
      const body = (await res.json()) as AssetList;
      setAssets((prev) => (cursor && prev ? [...prev, ...body.items] : body.items));
      setNextCursor(body.next_cursor);
    } catch (err: unknown) {
      if (isAbortError(err)) return;
      if (!cursor) setAssets(null);
      setListError(err instanceof Error ? err.message : "network error");
    }
  }, [albumId]);

  const loadAlbums = useCallback(async (signal?: AbortSignal) => {
    try {
      const res = await apiFetch("/api/v1/albums", { signal });
      if (!res.ok) return;
      const rows = (await res.json()) as LibraryAlbum[];
      setAlbums(rows);
      setAddAlbumId((prev) => (rows.some((row) => row.id === prev) ? prev : rows[0]?.id || ""));
    } catch (err: unknown) {
      if (isAbortError(err)) return;
    }
  }, []);

  const loadJobs = useCallback(async (signal?: AbortSignal) => {
    try {
      const res = await apiFetch("/api/v1/jobs", { signal });
      if (!res.ok) return;
      const body = (await res.json()) as { items: ImportJob[] };
      setJobs(body.items);
    } catch (err: unknown) {
      if (isAbortError(err)) return;
    }
  }, []);

  const applyJobResult = useCallback(
    async (job: ImportJob) => {
      if (job.status === "error") {
        setImportMessage(job.error || "import failed");
        await loadJobs();
        return;
      }
      if (job.status !== "done") return;
      if (job.kind === "thumb_batch") {
        setImportMessage(`Thumbs ready (${job.processed} files)`);
      } else {
        setImportMessage(
          `Imported ${job.assets} assets, ${job.albums} albums` +
            (job.import_root ? ` from ${job.import_root}` : ""),
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
      await loadAssets();
      await loadAlbums();
      await loadJobs();
    },
    [loadAssets, loadAlbums, loadJobs],
  );

  const pollImport = useCallback(
    async (signal?: AbortSignal, jobId?: string | null) => {
      try {
        for (;;) {
          const url = jobId ? `/api/v1/jobs/${jobId}` : "/api/v1/import/takeout/status";
          const res = await apiFetch(url, { signal });
          if (!res.ok) {
            setImportMessage(`HTTP ${res.status}`);
            return;
          }
          const job = (await res.json()) as ImportJob;
          if (job.status === "running") {
            const total = job.total || "?";
            const current = job.current ? ` · ${job.current}` : "";
            const label = job.kind === "thumb_batch" ? "Thumbs" : "Importing";
            setImportMessage(`${label} ${job.processed} / ${total}${current}`);
            await new Promise((r) => setTimeout(r, 500));
            continue;
          }
          await applyJobResult(job);
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
    const ctrl = new AbortController();
    fetch(apiMediaUrl("/api/v1/health"), { signal: ctrl.signal, credentials: "include" })
      .then(async (res) => {
        if (!res.ok) {
          setHealth({ kind: "fail", message: `HTTP ${res.status}` });
          return;
        }
        const body: unknown = await res.json();
        if (
          typeof body === "object" &&
          body !== null &&
          "status" in body &&
          (body as { status: unknown }).status === "ok"
        ) {
          setHealth({ kind: "ok" });
          return;
        }
        setHealth({ kind: "fail", message: "unexpected response" });
      })
      .catch((err: unknown) => {
        if (isAbortError(err)) return;
        setHealth({ kind: "fail", message: err instanceof Error ? err.message : "network error" });
      });
    return () => ctrl.abort("unmount");
  }, []);

  useEffect(() => {
    const ctrl = new AbortController();
    apiFetch("/api/v1/auth/status", { signal: ctrl.signal })
      .then(async (res) => {
        if (!res.ok) {
          setAuthReady(true);
          return;
        }
        const body = (await res.json()) as AuthStatus;
        setPlatform(body.platform);
        setNeedsSetup(body.needs_setup);
        if (body.platform === "qnap" && !body.me) {
          const login = await apiFetch("/api/v1/auth/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: "{}",
            signal: ctrl.signal,
          });
          if (login.ok) {
            setMe((await login.json()) as Me);
            setAuthReady(true);
            return;
          }
        }
        setMe(body.me);
        setAuthReady(true);
      })
      .catch((err: unknown) => {
        if (isAbortError(err)) return;
        setAuthReady(true);
      });
    return () => ctrl.abort("unmount");
  }, []);

  const loadWriteSources = useCallback(async (signal?: AbortSignal) => {
    try {
      const res = await apiFetch("/api/v1/sources", { signal });
      if (!res.ok) return;
      const rows = (await res.json()) as Source[];
      const writable = rows.filter((s) => s.kind === "folder" && s.perm === "write");
      setWriteSources(writable);
      setUploadSource((prev) => prev || writable[0]?.id || "");
    } catch (err: unknown) {
      if (isAbortError(err)) return;
    }
  }, [me]);

  useEffect(() => {
    if (!me) return;
    const ctrl = new AbortController();
    ignoreAbort(loadWriteSources(ctrl.signal));
    ignoreAbort(loadJobs(ctrl.signal));
    ignoreAbort(loadAlbums(ctrl.signal));
    return () => ctrl.abort("unmount");
  }, [me, loadJobs, loadWriteSources, loadAlbums]);

  useEffect(() => {
    if (!me) return;
    const ctrl = new AbortController();
    ignoreAbort(loadAssets(ctrl.signal));
    return () => ctrl.abort("unmount");
  }, [me, loadAssets]);

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
          setImportMessage(
            `Waiting for files from this device (${job.processed}${job.total ? ` / ${job.total}` : ""}). Refresh lost the folder picker — Cancel, or continue from the same app.`,
          );
          return;
        }
        setBusy(true);
        try {
          await pollImport(ctrl.signal, job.id);
        } finally {
          setBusy(false);
        }
      })
      .catch((err: unknown) => {
        if (isAbortError(err)) return;
      });
    return () => ctrl.abort("unmount");
  }, [me, pollImport]);

  useEffect(() => {
    if (!selected) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setSelected(null);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selected]);

  async function postTakeout(path: string, inspectOnly: boolean) {
    const url = inspectOnly
      ? "/api/v1/import/takeout/inspect"
      : "/api/v1/import/takeout";
    const res = await apiFetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: path || null }),
    });
    const body: unknown = await res.json().catch(() => null);
    return { res, body };
  }

  async function onCheck() {
    setBusy(true);
    setImportMessage(null);
    try {
      const { res, body } = await postTakeout(importPath, true);
      if (!res.ok) {
        const detail =
          typeof body === "object" && body && "detail" in body
            ? String((body as { detail: unknown }).detail)
            : `HTTP ${res.status}`;
        setInspect(null);
        setImportMessage(detail);
        return;
      }
      setInspect(body as TakeoutInspect);
    } catch (err: unknown) {
      setInspect(null);
      setImportMessage(err instanceof Error ? err.message : "check failed");
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

  async function onCancelPush() {
    if (!pushJobId) return;
    setBusy(true);
    try {
      const res = await apiFetch(`/api/v1/import/takeout/push/${pushJobId}/cancel`, { method: "POST" });
      if (!res.ok) {
        setImportMessage(await readDetail(res));
        return;
      }
      setPushJobId(null);
      setImportMessage("Upload cancelled");
      await loadJobs();
    } finally {
      setBusy(false);
    }
  }

  async function onDeviceImport() {
    if (clientFiles.length === 0) {
      setImportMessage("Choose a Takeout folder on this device first");
      return;
    }
    setBusy(true);
    setImportMessage(null);
    try {
      const startRes = await apiFetch("/api/v1/import/takeout/push", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_count: clientFiles.length }),
      });
      if (!startRes.ok) {
        setImportMessage(await readDetail(startRes));
        if (startRes.status === 409) {
          await attachReceivingPush();
        }
        return;
      }
      const started = (await startRes.json()) as ImportJob;
      if (!started.id) {
        setImportMessage("push did not return a job id");
        return;
      }
      setPushJobId(started.id);
      for (let i = 0; i < clientFiles.length; i += 1) {
        const file = clientFiles[i];
        const rel = clientDumpPath(file);
        setImportMessage(`Uploading ${i + 1} / ${clientFiles.length} · ${rel}`);
        const put = await apiFetch(
          `/api/v1/import/takeout/push/${started.id}/file?relative_path=${encodeURIComponent(rel)}`,
          {
            method: "POST",
            headers: { "Content-Type": "application/octet-stream" },
            body: file,
          },
        );
        if (!put.ok) {
          setImportMessage(await readDetail(put));
          return;
        }
      }
      setImportMessage("Upload complete · importing…");
      const fin = await apiFetch(`/api/v1/import/takeout/push/${started.id}/finish`, {
        method: "POST",
      });
      if (!fin.ok) {
        setImportMessage(await readDetail(fin));
        return;
      }
      const job = (await fin.json()) as ImportJob;
      if (job.status === "running") {
        await pollImport(undefined, job.id);
        return;
      }
      await applyJobResult(job);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "import failed";
      setImportMessage(
        msg === "Failed to fetch"
          ? "Connection to the API dropped while uploading to the NAS. This upload can't resume — click Cancel upload, then start again."
          : msg,
      );
    } finally {
      setBusy(false);
    }
  }

  async function onImport(event: FormEvent) {
    event.preventDefault();
    if (takeoutFrom === "device") {
      await onDeviceImport();
      return;
    }
    setBusy(true);
    setImportMessage(null);
    try {
      const { res, body } = await postTakeout(importPath, false);
      if (!res.ok) {
        const detail =
          typeof body === "object" && body && "detail" in body
            ? String((body as { detail: unknown }).detail)
            : `HTTP ${res.status}`;
        setImportMessage(detail);
        return;
      }
      const job = body as ImportJob;
      if (job.status === "running") {
        setImportMessage("Importing…");
        await pollImport(undefined, job.id);
        return;
      }
      await applyJobResult(job);
    } catch (err: unknown) {
      setImportMessage(err instanceof Error ? err.message : "import failed");
    } finally {
      setBusy(false);
    }
  }

  async function onGenerateThumbs() {
    setBusy(true);
    setImportMessage(null);
    try {
      const res = await apiFetch("/api/v1/thumbs/generate", { method: "POST" });
      const body: unknown = await res.json().catch(() => null);
      if (!res.ok) {
        const detail =
          typeof body === "object" && body && "detail" in body
            ? String((body as { detail: unknown }).detail)
            : `HTTP ${res.status}`;
        setImportMessage(detail);
        return;
      }
      const job = body as ImportJob;
      if (job.status === "running") {
        setImportMessage("Generating thumbs…");
        await pollImport(undefined, job.id);
        return;
      }
      await applyJobResult(job);
    } catch (err: unknown) {
      setImportMessage(err instanceof Error ? err.message : "thumbs failed");
    } finally {
      setBusy(false);
    }
  }

  async function onLogout() {
    await apiFetch("/api/v1/auth/logout", { method: "POST" });
    setMe(null);
    setAssets(null);
    setJobs(null);
    setNeedsSetup(false);
    setSettingsOpen(false);
    setAlbums(null);
    setOpenAlbum(null);
  }

  async function onUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const input = form.elements.namedItem("upload") as HTMLInputElement | null;
    const file = input?.files?.[0];
    if (!file || !uploadSource) return;
    setBusy(true);
    setImportMessage(null);
    try {
      const data = new FormData();
      data.set("source_id", uploadSource);
      data.set("file", file);
      const res = await apiFetch("/api/v1/assets", { method: "POST", body: data });
      if (!res.ok) {
        setImportMessage(await readDetail(res));
        return;
      }
      if (input) input.value = "";
      await loadAssets();
      await loadAlbums();
    } catch (err: unknown) {
      setImportMessage(err instanceof Error ? err.message : "upload failed");
    } finally {
      setBusy(false);
    }
  }

  async function onCreateAlbum(event: FormEvent) {
    event.preventDefault();
    setAlbumMessage(null);
    const res = await apiFetch("/api/v1/albums", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: newAlbumName }),
    });
    if (!res.ok) {
      setAlbumMessage(await readDetail(res));
      return;
    }
    const created = (await res.json()) as LibraryAlbum;
    setNewAlbumName("");
    setAddAlbumId(created.id);
    await loadAlbums();
  }

  async function onDeleteAlbum() {
    if (!openAlbum) return;
    setAlbumMessage(null);
    const res = await apiFetch(`/api/v1/albums/${openAlbum.id}`, { method: "DELETE" });
    if (!res.ok) {
      setAlbumMessage(await readDetail(res));
      return;
    }
    setOpenAlbum(null);
    setGalleryTab("albums");
    await loadAlbums();
  }

  async function onAddSelectedToAlbum() {
    if (!selected) return;
    const targetId =
      (addAlbumId && albums?.some((row) => row.id === addAlbumId) && addAlbumId) ||
      albums?.[0]?.id ||
      "";
    if (!targetId) {
      setAlbumMessage("Create an album first.");
      return;
    }
    const albumName = albums?.find((row) => row.id === targetId)?.name || "album";
    const before = albums?.find((row) => row.id === targetId)?.asset_count ?? 0;
    setAlbumMessage(null);
    const res = await apiFetch(`/api/v1/albums/${targetId}/assets`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ asset_ids: [selected.id] }),
    });
    if (!res.ok) {
      setAlbumMessage(await readDetail(res));
      return;
    }
    const body = (await res.json()) as LibraryAlbum;
    setAddAlbumId(targetId);
    setAlbumMessage(
      body.asset_count === before ? `Already in ${albumName}` : `Added to ${albumName}`,
    );
    await loadAlbums();
    if (albumId === targetId) await loadAssets();
  }

  async function onRemoveSelectedFromAlbum() {
    if (!selected || !openAlbum) return;
    setAlbumMessage(null);
    const res = await apiFetch(`/api/v1/albums/${openAlbum.id}/assets/${selected.id}`, {
      method: "DELETE",
    });
    if (!res.ok) {
      setAlbumMessage(await readDetail(res));
      return;
    }
    setSelected(null);
    await loadAssets();
    await loadAlbums();
  }

  if (!authReady) {
    return (
      <main>
        <BrandLockup lede="Library" />
        <div className="card">
          <p className="status loading">Checking session…</p>
        </div>
      </main>
    );
  }

  if (needsSetup && !me) {
    return (
      <AuthScreen
        mode="setup"
        platform={platform}
        onAuthed={(user) => {
          setMe(user);
          setNeedsSetup(false);
        }}
      />
    );
  }

  if (!me) {
    return (
      <AuthScreen
        mode="login"
        platform={platform}
        onAuthed={(user) => {
          setMe(user);
          setNeedsSetup(false);
        }}
      />
    );
  }

  return (
    <main>
      <div className="title-row">
        <BrandLockup lede="Library" />
        <div className="session-row">
          <span className="detail">
            {me.display_name || me.username} · {me.role}
          </span>
          <button type="button" onClick={() => setSettingsOpen((v) => !v)}>
            Settings
          </button>
          <button type="button" onClick={() => void onLogout()}>
            Sign out
          </button>
        </div>
      </div>

      <div className="card">
        {health.kind === "loading" && <p className="status loading">Checking API…</p>}
        {health.kind === "ok" && <p className="status ok">API OK</p>}
        {health.kind === "fail" && (
          <>
            <p className="status fail">API fail</p>
            <p className="detail">{health.message}</p>
          </>
        )}
      </div>

      {settingsOpen && (
        <SettingsPanel me={me} platform={platform} onClose={() => setSettingsOpen(false)} />
      )}

      <form className="card import" onSubmit={onImport}>
        <label>Takeout dump</label>
        <p className="detail">
          Server folder: the API reads a path on this NAS or PC. This device: the browser or
          phone uploads files (including a cloud folder opened in Files). mosaicWave does not
          sign in to Drive/iCloud. The library tmp folder contains all uploaded files.
        </p>
        <div className="choice-col">
          <label>
            <input
              type="radio"
              name="takeout-from"
              checked={takeoutFrom === "server"}
              onChange={() => setTakeoutFrom("server")}
              disabled={busy}
            />
            On this server
          </label>
          <label>
            <input
              type="radio"
              name="takeout-from"
              checked={takeoutFrom === "device"}
              onChange={() => setTakeoutFrom("device")}
              disabled={busy}
            />
            On this device
          </label>
        </div>
        {takeoutFrom === "server" ? (
          <>
            <label htmlFor="takeout-path">Folder on this NAS or PC</label>
            <div className="path-row">
              <input
                id="takeout-path"
                value={importPath}
                onChange={(e) => setImportPath(e.target.value)}
                placeholder="/share/…/Takeout (zips; you can delete after import)"
                autoComplete="off"
              />
              <button type="button" disabled={busy} onClick={() => setPickerOpen(true)}>
                Browse…
              </button>
            </div>
          </>
        ) : (
          <>
            <label htmlFor="takeout-device">Folder on this computer or phone</label>
            <input
              id="takeout-device"
              type="file"
              multiple
              onChange={(e) => setClientFiles(Array.from(e.target.files || []))}
              {...{ webkitdirectory: "", directory: "" }}
              disabled={busy}
            />
            <p className="detail">
              {clientFiles.length > 0
                ? `${clientFiles.length} file(s) selected`
                : "Pick the folder that contains takeout-*.zip (or an extracted Google Photos tree)."}
            </p>
          </>
        )}
        <div className="button-row">
          {takeoutFrom === "server" && (
            <button type="button" disabled={busy} onClick={() => void onCheck()}>
              {busy ? "Working…" : "Check folder"}
            </button>
          )}
          <button type="submit" disabled={busy}>
            {busy ? "Working…" : "Import Takeout"}
          </button>
          {pushJobId && (
            <button type="button" disabled={busy} onClick={() => void onCancelPush()}>
              Cancel upload
            </button>
          )}
        </div>
        {importMessage && <p className="detail import-progress">{importMessage}</p>}
        {inspect && (
          <div className="inspect">
            <p className={inspect.ready ? "status ok" : "status fail"}>
              {inspect.ready ? "Ready to import" : "Not ready"}
            </p>
            <p className="detail">
              {inspect.zip_count} zip(s)
              {inspect.import_root ? ` · import ${inspect.import_root}` : ""}
              {inspect.album_metadata_count
                ? ` · ${inspect.album_metadata_count} album metadata.json`
                : ""}
              {inspect.sidecar_without_media_count
                ? ` · ${inspect.sidecar_without_media_count} sidecar(s) without media`
                : ""}
            </p>
            {inspect.warnings.length > 0 && (
              <ul className="warn-list">
                {inspect.warnings.map((w, i) => (
                  <li key={`${w.code}-${i}`} className={w.severity}>
                    {w.message}
                  </li>
                ))}
              </ul>
            )}
            {inspect.sidecar_without_media.length > 0 && (
              <p className="detail missing-sample">
                Missing media (sample): {inspect.sidecar_without_media.join(", ")}
              </p>
            )}
          </div>
        )}
      </form>
      {pickerOpen && (
        <FolderPicker
          startPath={importPath}
          title="Select Takeout dump on the server"
          onSelect={(path) => {
            setImportPath(path);
            setPickerOpen(false);
          }}
          onClose={() => setPickerOpen(false)}
        />
      )}

      <section className="card">
        <h2>Jobs</h2>
        {jobs === null && <p className="detail">Loading jobs…</p>}
        {jobs && jobs.length === 0 && (
          <p className="detail">No task runs yet. Import Takeout records a job here.</p>
        )}
        {jobs && jobs.length > 0 && (
          <ul className="asset-list">
            {jobs.map((job) => (
              <li key={job.id ?? `${job.created_at}-${job.status}`}>
                <span className="name">
                  {jobKindLabel(job.kind)} · {job.status}
                  {job.status === "done" && job.kind === "thumb_batch" && job.processed
                    ? ` · ${job.processed} thumbs`
                    : ""}
                  {job.status === "done" && job.kind !== "thumb_batch" && job.assets
                    ? ` · ${job.assets} assets`
                    : ""}
                  {job.error ? ` · ${job.error}` : ""}
                </span>
                <span className="date">{formatJobWhen(job.started_at || job.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="card gallery">
        <div className="gallery-head">
          <h2>{openAlbum ? openAlbum.name : "Library"}</h2>
          <div className="button-row">
            <button
              type="button"
              className={!openAlbum && galleryTab === "timeline" ? "tab-on" : undefined}
              onClick={() => {
                setGalleryTab("timeline");
                setOpenAlbum(null);
              }}
            >
              Timeline
            </button>
            <button
              type="button"
              className={openAlbum || galleryTab === "albums" ? "tab-on" : undefined}
              onClick={() => {
                setGalleryTab("albums");
                setOpenAlbum(null);
              }}
            >
              Albums
            </button>
            {openAlbum ? (
              <button type="button" disabled={busy} onClick={() => void onDeleteAlbum()}>
                Delete album
              </button>
            ) : (
              <button type="button" disabled={busy} onClick={() => void onGenerateThumbs()}>
                Generate thumbs
              </button>
            )}
          </div>
        </div>
        {albumMessage && (
          <p className={albumMessage.startsWith("Added") || albumMessage.startsWith("Already") ? "status ok" : "status fail"}>
            {albumMessage}
          </p>
        )}
        {!openAlbum && galleryTab === "albums" ? (
          <>
            <form className="upload-row" onSubmit={(event) => void onCreateAlbum(event)}>
              <input
                value={newAlbumName}
                onChange={(e) => setNewAlbumName(e.target.value)}
                placeholder="New album name"
                maxLength={255}
              />
              <button type="submit" disabled={busy || !newAlbumName.trim()}>
                Create album
              </button>
            </form>
            {albums === null && <p className="detail">Loading albums…</p>}
            {albums && albums.length === 0 && (
              <p className="detail">No albums yet. Takeout folders become albums; you can also create one here.</p>
            )}
            {albums && albums.length > 0 && (
              <div className="thumb-grid">
                {albums.map((album) => (
                  <button
                    type="button"
                    key={album.id}
                    className="thumb album-tile"
                    title={album.name}
                    onClick={() => {
                      setOpenAlbum(album);
                      setGalleryTab("timeline");
                    }}
                  >
                    {album.cover_asset_id ? (
                      <img src={`/api/v1/assets/${album.cover_asset_id}/thumb`} alt="" />
                    ) : (
                      <span className="album-empty">No cover</span>
                    )}
                    <span className="album-caption">
                      {album.name} · {album.asset_count}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </>
        ) : (
          <>
        {writeSources.length > 0 && (
          <form className="upload-row" onSubmit={onUpload}>
            <select
              value={uploadSource}
              onChange={(e) => setUploadSource(e.target.value)}
              aria-label="Upload source"
            >
              {writeSources.map((source) => (
                <option key={source.id} value={source.id}>
                  {source.owned ? "Your library" : "Shared"} · {source.asset_count} assets
                </option>
              ))}
            </select>
            <input name="upload" type="file" accept="image/*,video/*" />
            <button type="submit" disabled={busy}>
              Upload
            </button>
          </form>
        )}
        {listError && <p className="status fail">{listError}</p>}
        {!listError && assets === null && <p className="detail">Loading library…</p>}
        {!listError && assets && assets.length === 0 && (
          <p className="detail">
            {openAlbum
              ? "This album is empty. Open a photo and use Add to album."
              : "Empty library. Import a Takeout folder to seed SQLite."}
          </p>
        )}
        {!listError && assets && assets.length > 0 && (
          <>
            {timelineBlocks(assets).map((group) => (
              <div key={group.day} className="day-block">
                {group.month && <h3 className="timeline-month">{group.month}</h3>}
                <h4 className="timeline-day">{group.day}</h4>
                <div className="thumb-grid">
                  {group.items.map((asset) => (
                    <button
                      type="button"
                      key={asset.id}
                      className="thumb"
                      onClick={() => setSelected(asset)}
                      title={asset.filename}
                    >
                          <img
                            src={`/api/v1/assets/${asset.id}/thumb?rev=${asset.thumb_rev}`}
                            alt={asset.filename}
                          />
                    </button>
                  ))}
                </div>
              </div>
            ))}
            {nextCursor && (
              <button
                type="button"
                className="load-more"
                disabled={busy}
                onClick={() => void loadAssets(undefined, nextCursor)}
              >
                Load more
              </button>
            )}
          </>
        )}
          </>
        )}
      </section>
      {selected && (
        <div
          className="detail-backdrop"
          onClick={() => setSelected(null)}
          role="presentation"
        >
          <div
            className="detail-card"
            onClick={(event) => event.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-label={selected.filename}
          >
            <div className="detail-media">
              {isVideo(selected) ? (
                <VideoPlayer
                  assetId={selected.id}
                  poster={`/api/v1/assets/${selected.id}/thumb?rev=${selected.thumb_rev}`}
                  title={selected.filename}
                />
              ) : (
                <img src={stillSrc(selected)} alt={selected.filename} />
              )}
            </div>
            <div className="detail-meta">
              <p className="name">{selected.filename}</p>
              <p className="detail">
                {isVideo(selected)
                  ? formatDate(selected.taken_at)
                  : `${formatDate(selected.taken_at)} · ${formatBytes(selected.size)}`}
              </p>
              {albumMessage && (
                <p
                  className={
                    albumMessage.startsWith("Added") || albumMessage.startsWith("Already")
                      ? "status ok"
                      : "status fail"
                  }
                >
                  {albumMessage}
                </p>
              )}
              <div className="button-row">
                <a href={assetFileUrl(selected.id, true)}>Download original</a>
                {albums && albums.length > 0 && (
                  <>
                    <select
                      aria-label="Album"
                      value={
                        albums.some((album) => album.id === addAlbumId)
                          ? addAlbumId
                          : albums[0].id
                      }
                      onChange={(e) => setAddAlbumId(e.target.value)}
                    >
                      {albums.map((album) => (
                        <option key={album.id} value={album.id}>
                          {album.name}
                        </option>
                      ))}
                    </select>
                    <button type="button" onClick={() => void onAddSelectedToAlbum()}>
                      Add to album
                    </button>
                  </>
                )}
                {openAlbum && (
                  <button type="button" onClick={() => void onRemoveSelectedFromAlbum()}>
                    Remove from album
                  </button>
                )}
                <button type="button" onClick={() => setSelected(null)}>
                  Close
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
