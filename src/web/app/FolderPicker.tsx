"use client";

import { useCallback, useEffect, useState } from "react";

import { apiFetch } from "./api";

type FsEntry = {
  name: string;
  path: string;
  is_dir: boolean;
};

type FsList = {
  path: string;
  parent: string | null;
  entries: FsEntry[];
  takeout_zip_count: number;
  warning?: string | null;
};

type Props = {
  startPath: string;
  onSelect: (path: string) => void;
  onClose: () => void;
  title?: string;
};

function parentPath(path: string): string {
  const trimmed = path.replace(/[\\/]+$/, "");
  if (!trimmed) return "";
  const unix = trimmed.replace(/\\/g, "/");
  const idx = unix.lastIndexOf("/");
  if (idx <= 0) return "";
  return unix.slice(0, idx);
}

export function FolderPicker({ startPath, onSelect, onClose, title = "Select folder on the server" }: Props) {
  const [current, setCurrent] = useState(startPath);
  const [listing, setListing] = useState<FsList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (path: string) => {
    setLoading(true);
    setError(null);
    let next: string = path;
    let note: string | null = null;
    try {
      for (;;) {
        const qs = next ? `?path=${encodeURIComponent(next)}` : "";
        const res = await apiFetch(`/api/v1/fs/list${qs}`);
        const body: unknown = await res.json().catch(() => null);
        const detail =
          typeof body === "object" && body && "detail" in body
            ? String((body as { detail: unknown }).detail)
            : `HTTP ${res.status}`;
        if (res.status === 401) {
          setListing(null);
          setError(
            "Not signed in. Close Settings, reload, and sign in. Do not Create admin if a NAS move just failed.",
          );
          return;
        }
        if (res.ok && body && typeof body === "object") {
          const data = body as FsList;
          setListing(data);
          setCurrent(data.path || next || "");
          setError(data.warning || note);
          return;
        }
        if (!next) {
          setListing(null);
          setError(detail);
          return;
        }
        note = `${detail} — opened parent of ${next}`;
        next = parentPath(next);
      }
    } catch (err: unknown) {
      setListing(null);
      setError(err instanceof Error ? err.message : "browse failed");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(startPath);
  }, [load, startPath]);

  const dirs = listing?.entries.filter((e) => e.is_dir) ?? [];
  const zips = listing?.entries.filter((e) => !e.is_dir) ?? [];
  const usable = (listing?.path || current || "").trim();

  return (
    <div className="picker-backdrop" role="presentation" onClick={onClose}>
      <div
        className="picker"
        role="dialog"
        aria-labelledby="picker-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="picker-title">{title}</h2>
        <p className="detail">
          Folders on the mosaicWave host (this NAS or PC), not the browser computer. Choosing a
          folder only fills the path box — Save folder in Settings is what moves the library.
        </p>
        <p className="picker-path">{listing ? listing.path || "Places" : current || ""}</p>
        {error && <p className="status fail">{error}</p>}
        {loading && <p className="detail">Loading…</p>}
        {!loading && listing && (
          <ul className="picker-list">
            {listing.path !== "" && (
              <li>
                <button type="button" className="picker-item" onClick={() => void load("")}>
                  Places
                </button>
              </li>
            )}
            {listing.parent !== null && (
              <li>
                <button type="button" className="picker-item" onClick={() => void load(listing.parent ?? "")}>
                  ..
                </button>
              </li>
            )}
            {dirs.map((entry) => (
              <li key={entry.path}>
                <button type="button" className="picker-item" onClick={() => void load(entry.path)}>
                  {entry.name}/
                </button>
              </li>
            ))}
            {zips.map((entry) => (
              <li key={entry.path} className="picker-file">
                {entry.name}
              </li>
            ))}
            {!dirs.length && !zips.length && (
              <li className="detail">No subfolders in this directory.</li>
            )}
          </ul>
        )}
        {listing && listing.takeout_zip_count > 0 && (
          <p className="detail">{listing.takeout_zip_count} takeout zip(s) in this folder</p>
        )}
        <div className="button-row">
          <button type="button" disabled={!usable} onClick={() => usable && onSelect(usable)}>
            Use this folder
          </button>
          <button type="button" onClick={onClose}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
