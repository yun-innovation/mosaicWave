import type { DumpWarning } from "./api";

export type Asset = {
  id: string;
  filename: string;
  taken_at: string | null;
  mime: string | null;
  size: number;
  thumb_rev: number;
};

export type AssetList = {
  items: Asset[];
  next_cursor: string | null;
};

export type LibraryAlbum = {
  id: string;
  name: string;
  cover_asset_id: string | null;
  asset_count: number;
};

export type TakeoutInspect = {
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

export type ImportJob = {
  id: string | null;
  kind: string | null;
  status: "idle" | "running" | "done" | "error" | "cancelled" | string;
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

export type Notice = { text: string; ok: boolean };

/** `GET /api/v1/jobs/running` (admin): anyone's running job, without file names. */
export type RunningJob = {
  id: string;
  kind: string | null;
  status: string;
  started_at: string | null;
  processed: number;
  total: number;
  owner: string | null;
  /** A stop was requested; the job ends within moments. */
  cancelling: boolean;
};

/** `GET /api/v1/maintenance`: `draining` = on, but a running job is still finishing. */
export type MaintenanceStatus = {
  state: "off" | "draining" | "on";
  reason: string;
  since: string | null;
};

export type BackupItem = {
  name: string;
  size: number;
  created_at: string;
  app_version: string;
  schema_version: number;
  include_thumbs: boolean;
  users: number;
  assets: number;
  albums: number;
  file_count: number;
  total_bytes: number;
  warnings: string[];
};

export function isAbortError(err: unknown): boolean {
  return typeof err === "object" && err !== null && "name" in err && (err as { name: string }).name === "AbortError";
}

export function ignoreAbort(task: Promise<unknown>): void {
  void task.catch((err: unknown) => {
    if (!isAbortError(err)) throw err;
  });
}

export function isVideo(asset: Asset): boolean {
  if (asset.mime?.startsWith("video/")) return true;
  return /\.(mp4|mov|m4v|avi|mkv|webm|3gp)$/i.test(asset.filename);
}

function needsJpegPreview(asset: Asset): boolean {
  if (isVideo(asset)) return false;
  if (/\.(heic|heif|tif|tiff|dng)$/i.test(asset.filename)) return true;
  return /image\/(heic|heif|tiff|x-adobe-dng)/i.test(asset.mime || "");
}

export function stillSrc(asset: Asset): string {
  if (needsJpegPreview(asset)) {
    return `/api/v1/assets/${asset.id}/preview?rev=${asset.thumb_rev}`;
  }
  return `/api/v1/assets/${asset.id}/file`;
}

export function thumbSrc(asset: Asset): string {
  return `/api/v1/assets/${asset.id}/thumb?rev=${asset.thumb_rev}`;
}
