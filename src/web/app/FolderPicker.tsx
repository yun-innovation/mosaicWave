"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { apiFetch, errorText } from "./api";
import i18n from "./i18n";

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
  /** `takeout` (default): choose a folder. `backup`: choose a zip file; any zip is listed (admin only). */
  kind?: "takeout" | "backup";
  intro?: string;
};

function parentPath(path: string): string {
  const trimmed = path.replace(/[\\/]+$/, "");
  if (!trimmed) return "";
  const unix = trimmed.replace(/\\/g, "/");
  const idx = unix.lastIndexOf("/");
  if (idx <= 0) return "";
  return unix.slice(0, idx);
}

export function FolderPicker({ startPath, onSelect, onClose, title, kind = "takeout", intro }: Props) {
  const pickFile = kind === "backup";
  const { t } = useTranslation("common");
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
        const params = new URLSearchParams();
        if (next) params.set("path", next);
        if (pickFile) params.set("kind", "backup");
        const qs = params.size > 0 ? `?${params.toString()}` : "";
        const res = await apiFetch(`/api/v1/fs/list${qs}`);
        const body: unknown = await res.json().catch(() => null);
        const detail = errorText(body, res.status);
        if (res.status === 401) {
          setListing(null);
          setError(i18n.t("common:picker.notSignedIn"));
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
        note = i18n.t("common:picker.openedParent", { detail, path: next });
        next = parentPath(next);
      }
    } catch (err: unknown) {
      setListing(null);
      setError(err instanceof Error ? err.message : i18n.t("common:picker.browseFailed"));
    } finally {
      setLoading(false);
    }
  }, [pickFile]);

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
        <h2 id="picker-title">{title ?? t("picker.title")}</h2>
        <p className="detail">{intro ?? t("picker.intro")}</p>
        <p className="picker-path">{listing ? listing.path || t("picker.places") : current || ""}</p>
        {error && <p className="status fail">{error}</p>}
        {loading && <p className="detail">{t("loading")}</p>}
        {!loading && listing && (
          <ul className="picker-list">
            {listing.path !== "" && (
              <li>
                <button type="button" className="picker-item" onClick={() => void load("")}>
                  {t("picker.places")}
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
              <li key={entry.path} className={pickFile ? undefined : "picker-file"}>
                {pickFile ? (
                  <button type="button" className="picker-item" onClick={() => onSelect(entry.path)}>
                    {entry.name}
                  </button>
                ) : (
                  entry.name
                )}
              </li>
            ))}
            {!dirs.length && !zips.length && (
              <li className="detail">{t("picker.noSubfolders")}</li>
            )}
          </ul>
        )}
        {listing && !pickFile && listing.takeout_zip_count > 0 && (
          <p className="detail">{t("picker.zipCount", { count: listing.takeout_zip_count })}</p>
        )}
        <div className="button-row">
          {!pickFile && (
            <button type="button" disabled={!usable} onClick={() => usable && onSelect(usable)}>
              {t("picker.use")}
            </button>
          )}
          <button type="button" onClick={onClose}>
            {t("cancel")}
          </button>
        </div>
      </div>
    </div>
  );
}
