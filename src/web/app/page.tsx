"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { BrandLockup } from "./BrandLockup";
import { FolderPicker } from "./FolderPicker";
import { AuthScreen } from "./AuthScreen";
import { SettingsPanel } from "./SettingsPanel";
import { VideoPlayer } from "./VideoPlayer";
import {
  apiFetch,
  apiMediaUrl,
  assetFileUrl,
  AuthStatus,
  DumpWarning,
  errorText,
  Me,
  readDetail,
  Source,
  warningText,
} from "./api";
import { dayKey, formatBytes, formatDate, formatDateTime, formatDay, formatMonth } from "./format";
import i18n, { applyLocale, isLocale, rememberLocale, resolveLocale } from "./i18n";

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

function jobKindLabel(kind: string | null): string {
  if (kind === "takeout_import") return i18n.t("import:jobs.kind.takeout_import");
  if (kind === "thumb_batch") return i18n.t("import:jobs.kind.thumb_batch");
  return kind || i18n.t("import:jobs.kind.unknown");
}

const JOB_STATUSES = ["idle", "queued", "running", "done", "error"] as const;

function jobStatusLabel(status: string): string {
  const known = JOB_STATUSES.find((s) => s === status);
  return known ? i18n.t(`import:jobs.status.${known}`) : status;
}

type AlbumMessage = { text: string; ok: boolean };

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

const UNKNOWN_DAY = "unknown";

type DayGroup = { day: string; items: Asset[] };

function groupByDay(assets: Asset[]): DayGroup[] {
  const map = new Map<string, Asset[]>();
  const order: string[] = [];
  for (const asset of assets) {
    const key = dayKey(asset.taken_at) ?? UNKNOWN_DAY;
    if (!map.has(key)) {
      map.set(key, []);
      order.push(key);
    }
    map.get(key)!.push(asset);
  }
  return order.map((day) => ({ day, items: map.get(day)! }));
}

type TimelineBlock = { key: string; month: string | null; day: string; items: Asset[] };

function timelineBlocks(assets: Asset[]): TimelineBlock[] {
  const unknown = i18n.t("common:unknownDate");
  let lastMonth = "";
  return groupByDay(assets).map((group) => {
    const known = group.day !== UNKNOWN_DAY;
    const month = known ? formatMonth(group.day) : unknown;
    const heading = month !== lastMonth ? month : null;
    lastMonth = month;
    return {
      key: group.day,
      month: heading,
      day: known ? formatDay(group.day) : unknown,
      items: group.items,
    };
  });
}

export default function Home() {
  const { t } = useTranslation(["gallery", "import", "common"]);
  const [health, setHealth] = useState<HealthState>({ kind: "loading" });
  const [me, setMe] = useState<Me | null>(null);
  const savedLocale = me?.locale ?? null;

  useEffect(() => {
    if (isLocale(savedLocale)) rememberLocale(savedLocale);
    void applyLocale(resolveLocale(savedLocale));
  }, [savedLocale]);
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
  const [albumMessage, setAlbumMessage] = useState<AlbumMessage | null>(null);

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
        setListError(await readDetail(res));
        return;
      }
      const body = (await res.json()) as AssetList;
      setAssets((prev) => (cursor && prev ? [...prev, ...body.items] : body.items));
      setNextCursor(body.next_cursor);
    } catch (err: unknown) {
      if (isAbortError(err)) return;
      if (!cursor) setAssets(null);
      setListError(err instanceof Error ? err.message : i18n.t("common:networkError"));
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
        setImportMessage(job.error || i18n.t("import:fail.import"));
        await loadJobs();
        return;
      }
      if (job.status !== "done") return;
      if (job.kind === "thumb_batch") {
        setImportMessage(i18n.t("import:result.thumbsReady", { count: job.processed }));
      } else {
        const counts = {
          assets: i18n.t("import:result.assets", { count: job.assets }),
          albums: i18n.t("import:result.albums", { count: job.albums }),
        };
        setImportMessage(
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
            setImportMessage(await readDetail(res));
            return;
          }
          const job = (await res.json()) as ImportJob;
          if (job.status === "running") {
            const values = { processed: job.processed, total: job.total || "?" };
            const label =
              job.kind === "thumb_batch"
                ? i18n.t("import:progress.thumbs", values)
                : i18n.t("import:progress.importing", values);
            setImportMessage(job.current ? `${label} · ${job.current}` : label);
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
          setHealth({ kind: "fail", message: i18n.t("common:httpStatus", { status: res.status }) });
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
        setHealth({ kind: "fail", message: i18n.t("common:health.unexpected") });
      })
      .catch((err: unknown) => {
        if (isAbortError(err)) return;
        setHealth({
          kind: "fail",
          message: err instanceof Error ? err.message : i18n.t("common:networkError"),
        });
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
            i18n.t("import:progress.waitingDevice", {
              progress: job.total ? `${job.processed} / ${job.total}` : String(job.processed),
            }),
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
        setInspect(null);
        setImportMessage(errorText(body, res.status));
        return;
      }
      setInspect(body as TakeoutInspect);
    } catch (err: unknown) {
      setInspect(null);
      setImportMessage(err instanceof Error ? err.message : t("import:fail.check"));
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
      setImportMessage(t("import:result.cancelled"));
      await loadJobs();
    } finally {
      setBusy(false);
    }
  }

  async function onDeviceImport() {
    if (clientFiles.length === 0) {
      setImportMessage(t("import:fail.chooseFolder"));
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
        setImportMessage(t("import:fail.noJobId"));
        return;
      }
      setPushJobId(started.id);
      for (let i = 0; i < clientFiles.length; i += 1) {
        const file = clientFiles[i];
        const rel = clientDumpPath(file);
        setImportMessage(
          t("import:progress.uploading", { index: i + 1, total: clientFiles.length, path: rel }),
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
          setImportMessage(await readDetail(put));
          return;
        }
      }
      setImportMessage(t("import:progress.uploadComplete"));
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
      const msg = err instanceof Error ? err.message : t("import:fail.import");
      setImportMessage(msg === "Failed to fetch" ? t("import:fail.connectionDropped") : msg);
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
        setImportMessage(errorText(body, res.status));
        return;
      }
      const job = body as ImportJob;
      if (job.status === "running") {
        setImportMessage(t("import:progress.importingShort"));
        await pollImport(undefined, job.id);
        return;
      }
      await applyJobResult(job);
    } catch (err: unknown) {
      setImportMessage(err instanceof Error ? err.message : t("import:fail.import"));
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
        setImportMessage(errorText(body, res.status));
        return;
      }
      const job = body as ImportJob;
      if (job.status === "running") {
        setImportMessage(t("import:progress.generatingThumbs"));
        await pollImport(undefined, job.id);
        return;
      }
      await applyJobResult(job);
    } catch (err: unknown) {
      setImportMessage(err instanceof Error ? err.message : t("import:fail.thumbs"));
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
      setImportMessage(err instanceof Error ? err.message : t("import:fail.upload"));
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
      setAlbumMessage({ text: await readDetail(res), ok: false });
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
      setAlbumMessage({ text: await readDetail(res), ok: false });
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
      setAlbumMessage({ text: t("gallery:album.createFirst"), ok: false });
      return;
    }
    const albumName =
      albums?.find((row) => row.id === targetId)?.name || t("gallery:album.fallbackName");
    const before = albums?.find((row) => row.id === targetId)?.asset_count ?? 0;
    setAlbumMessage(null);
    const res = await apiFetch(`/api/v1/albums/${targetId}/assets`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ asset_ids: [selected.id] }),
    });
    if (!res.ok) {
      setAlbumMessage({ text: await readDetail(res), ok: false });
      return;
    }
    const body = (await res.json()) as LibraryAlbum;
    setAddAlbumId(targetId);
    setAlbumMessage({
      text:
        body.asset_count === before
          ? t("gallery:album.alreadyIn", { name: albumName })
          : t("gallery:album.addedTo", { name: albumName }),
      ok: true,
    });
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
      setAlbumMessage({ text: await readDetail(res), ok: false });
      return;
    }
    setSelected(null);
    await loadAssets();
    await loadAlbums();
  }

  if (!authReady) {
    return (
      <main>
        <BrandLockup lede={t("common:lede")} />
        <div className="card">
          <p className="status loading">{t("common:session.checking")}</p>
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
        <BrandLockup lede={t("common:lede")} />
        <div className="session-row">
          <span className="detail">
            {t("common:session.who", {
              name: me.display_name || me.username,
              role: me.role === "admin" ? t("common:role.admin") : t("common:role.member"),
            })}
          </span>
          <button type="button" onClick={() => setSettingsOpen((v) => !v)}>
            {t("common:session.settings")}
          </button>
          <button type="button" onClick={() => void onLogout()}>
            {t("common:session.signOut")}
          </button>
        </div>
      </div>

      <div className="card">
        {health.kind === "loading" && <p className="status loading">{t("common:health.checking")}</p>}
        {health.kind === "ok" && <p className="status ok">{t("common:health.ok")}</p>}
        {health.kind === "fail" && (
          <>
            <p className="status fail">{t("common:health.fail")}</p>
            <p className="detail">{health.message}</p>
          </>
        )}
      </div>

      {settingsOpen && (
        <SettingsPanel
          me={me}
          platform={platform}
          onClose={() => setSettingsOpen(false)}
          onMe={setMe}
        />
      )}

      <form className="card import" onSubmit={onImport}>
        <label>{t("import:title")}</label>
        <p className="detail">{t("import:intro")}</p>
        <div className="choice-col">
          <label>
            <input
              type="radio"
              name="takeout-from"
              checked={takeoutFrom === "server"}
              onChange={() => setTakeoutFrom("server")}
              disabled={busy}
            />
            {t("import:from.server")}
          </label>
          <label>
            <input
              type="radio"
              name="takeout-from"
              checked={takeoutFrom === "device"}
              onChange={() => setTakeoutFrom("device")}
              disabled={busy}
            />
            {t("import:from.device")}
          </label>
        </div>
        {takeoutFrom === "server" ? (
          <>
            <label htmlFor="takeout-path">{t("import:serverPath.label")}</label>
            <div className="path-row">
              <input
                id="takeout-path"
                value={importPath}
                onChange={(e) => setImportPath(e.target.value)}
                placeholder={t("import:serverPath.placeholder")}
                autoComplete="off"
              />
              <button type="button" disabled={busy} onClick={() => setPickerOpen(true)}>
                {t("common:browse")}
              </button>
            </div>
          </>
        ) : (
          <>
            <label htmlFor="takeout-device">{t("import:device.label")}</label>
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
                ? t("import:device.selected", { count: clientFiles.length })
                : t("import:device.hint")}
            </p>
          </>
        )}
        <div className="button-row">
          {takeoutFrom === "server" && (
            <button type="button" disabled={busy} onClick={() => void onCheck()}>
              {busy ? t("common:working") : t("import:check")}
            </button>
          )}
          <button type="submit" disabled={busy}>
            {busy ? t("common:working") : t("import:import")}
          </button>
          {pushJobId && (
            <button type="button" disabled={busy} onClick={() => void onCancelPush()}>
              {t("import:cancelUpload")}
            </button>
          )}
        </div>
        {importMessage && <p className="detail import-progress">{importMessage}</p>}
        {inspect && (
          <div className="inspect">
            <p className={inspect.ready ? "status ok" : "status fail"}>
              {inspect.ready ? t("import:inspect.ready") : t("import:inspect.notReady")}
            </p>
            <p className="detail">
              {[
                t("import:inspect.zips", { count: inspect.zip_count }),
                inspect.import_root ? t("import:inspect.importRoot", { path: inspect.import_root }) : null,
                inspect.album_metadata_count
                  ? t("import:inspect.albumMetadata", { count: inspect.album_metadata_count })
                  : null,
                inspect.sidecar_without_media_count
                  ? t("import:inspect.sidecars", { count: inspect.sidecar_without_media_count })
                  : null,
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
            {inspect.warnings.length > 0 && (
              <ul className="warn-list">
                {inspect.warnings.map((w, i) => (
                  <li key={`${w.code}-${i}`} className={w.severity}>
                    {warningText(w)}
                  </li>
                ))}
              </ul>
            )}
            {inspect.sidecar_without_media.length > 0 && (
              <p className="detail missing-sample">
                {t("import:inspect.missingSample", { names: inspect.sidecar_without_media.join(", ") })}
              </p>
            )}
          </div>
        )}
      </form>
      {pickerOpen && (
        <FolderPicker
          startPath={importPath}
          title={t("import:serverPath.pickerTitle")}
          onSelect={(path) => {
            setImportPath(path);
            setPickerOpen(false);
          }}
          onClose={() => setPickerOpen(false)}
        />
      )}

      <section className="card">
        <h2>{t("import:jobs.title")}</h2>
        {jobs === null && <p className="detail">{t("import:jobs.loading")}</p>}
        {jobs && jobs.length === 0 && <p className="detail">{t("import:jobs.empty")}</p>}
        {jobs && jobs.length > 0 && (
          <ul className="asset-list">
            {jobs.map((job) => (
              <li key={job.id ?? `${job.created_at}-${job.status}`}>
                <span className="name">
                  {jobKindLabel(job.kind)} · {jobStatusLabel(job.status)}
                  {job.status === "done" && job.kind === "thumb_batch" && job.processed
                    ? ` · ${t("import:jobs.thumbs", { count: job.processed })}`
                    : ""}
                  {job.status === "done" && job.kind !== "thumb_batch" && job.assets
                    ? ` · ${t("import:result.assets", { count: job.assets })}`
                    : ""}
                  {job.error ? ` · ${job.error}` : ""}
                </span>
                <span className="date">{formatDateTime(job.started_at || job.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="card gallery">
        <div className="gallery-head">
          <h2>{openAlbum ? openAlbum.name : t("gallery:title")}</h2>
          <div className="button-row">
            <button
              type="button"
              className={!openAlbum && galleryTab === "timeline" ? "tab-on" : undefined}
              onClick={() => {
                setGalleryTab("timeline");
                setOpenAlbum(null);
              }}
            >
              {t("gallery:tab.timeline")}
            </button>
            <button
              type="button"
              className={openAlbum || galleryTab === "albums" ? "tab-on" : undefined}
              onClick={() => {
                setGalleryTab("albums");
                setOpenAlbum(null);
              }}
            >
              {t("gallery:tab.albums")}
            </button>
            {openAlbum ? (
              <button type="button" disabled={busy} onClick={() => void onDeleteAlbum()}>
                {t("gallery:deleteAlbum")}
              </button>
            ) : (
              <button type="button" disabled={busy} onClick={() => void onGenerateThumbs()}>
                {t("gallery:generateThumbs")}
              </button>
            )}
          </div>
        </div>
        {albumMessage && (
          <p className={albumMessage.ok ? "status ok" : "status fail"}>{albumMessage.text}</p>
        )}
        {!openAlbum && galleryTab === "albums" ? (
          <>
            <form className="upload-row" onSubmit={(event) => void onCreateAlbum(event)}>
              <input
                value={newAlbumName}
                onChange={(e) => setNewAlbumName(e.target.value)}
                placeholder={t("gallery:album.newPlaceholder")}
                maxLength={255}
              />
              <button type="submit" disabled={busy || !newAlbumName.trim()}>
                {t("gallery:album.create")}
              </button>
            </form>
            {albums === null && <p className="detail">{t("gallery:album.loading")}</p>}
            {albums && albums.length === 0 && <p className="detail">{t("gallery:album.empty")}</p>}
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
                      <span className="album-empty">{t("gallery:album.noCover")}</span>
                    )}
                    <span className="album-caption">
                      {t("gallery:album.caption", { name: album.name, count: album.asset_count })}
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
              aria-label={t("gallery:upload.sourceLabel")}
            >
              {writeSources.map((source) => (
                <option key={source.id} value={source.id}>
                  {t("gallery:upload.sourceOption", {
                    owner: source.owned ? t("gallery:upload.yourLibrary") : t("gallery:upload.shared"),
                    count: source.asset_count,
                  })}
                </option>
              ))}
            </select>
            <input name="upload" type="file" accept="image/*,video/*" />
            <button type="submit" disabled={busy}>
              {t("gallery:upload.submit")}
            </button>
          </form>
        )}
        {listError && <p className="status fail">{listError}</p>}
        {!listError && assets === null && <p className="detail">{t("gallery:loadingLibrary")}</p>}
        {!listError && assets && assets.length === 0 && (
          <p className="detail">
            {openAlbum ? t("gallery:emptyAlbum") : t("gallery:emptyLibrary")}
          </p>
        )}
        {!listError && assets && assets.length > 0 && (
          <>
            {timelineBlocks(assets).map((group) => (
              <div key={group.key} className="day-block">
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
                {t("gallery:loadMore")}
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
                {[
                  formatDate(selected.taken_at) ?? t("common:unknownDate"),
                  isVideo(selected) ? null : formatBytes(selected.size),
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
              {albumMessage && (
                <p className={albumMessage.ok ? "status ok" : "status fail"}>{albumMessage.text}</p>
              )}
              <div className="button-row">
                <a href={assetFileUrl(selected.id, true)}>{t("gallery:detail.download")}</a>
                {albums && albums.length > 0 && (
                  <>
                    <select
                      aria-label={t("gallery:detail.albumLabel")}
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
                      {t("gallery:detail.addToAlbum")}
                    </button>
                  </>
                )}
                {openAlbum && (
                  <button type="button" onClick={() => void onRemoveSelectedFromAlbum()}>
                    {t("gallery:detail.removeFromAlbum")}
                  </button>
                )}
                <button type="button" onClick={() => setSelected(null)}>
                  {t("common:close")}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
