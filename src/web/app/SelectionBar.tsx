"use client";

import { useState } from "react";
import { useTranslation } from "react-i18next";

import { apiFetch, readDetail } from "./api";
import { ExportStarter } from "./ExportPanel";
import { LibraryAlbum, Notice } from "./types";

// The album endpoint takes at most 200 photos per request.
const ALBUM_CHUNK = 200;

type Props = {
  /** The picked photos, in the order they were picked. */
  ids: string[];
  /** How many photos the page has loaded, for "Select all shown". */
  shownCount: number;
  albums: LibraryAlbum[] | null;
  /** Album the list is showing; enables Remove from album. */
  albumId: string | null;
  onSelectShown: () => void;
  onClear: () => void;
  onDone: () => void;
  /** Album membership changed; `removed` means photos left the open album. */
  onChanged: (removed: boolean) => void;
};

export function SelectionBar({ ids, shownCount, albums, albumId, onSelectShown, onClear, onDone, onChanged }: Props) {
  const { t } = useTranslation(["gallery", "backup", "common"]);
  const [target, setTarget] = useState("");
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);

  const album = albums?.find((row) => row.id === target) ?? albums?.[0] ?? null;
  const none = ids.length === 0;

  async function addToAlbum() {
    if (album) await addTo(album.id, album.name);
  }

  async function createAlbum() {
    const name = newName.trim();
    if (!name || none) return;
    setBusy(true);
    setNotice(null);
    try {
      const res = await apiFetch("/api/v1/albums", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (!res.ok) {
        setNotice({ text: await readDetail(res), ok: false });
        return;
      }
      const created = (await res.json()) as LibraryAlbum;
      setNewName("");
      setTarget(created.id);
      onChanged(false); // the album exists now, even if adding to it fails below
      await addTo(created.id, created.name, true);
    } finally {
      setBusy(false);
    }
  }

  async function addTo(albumKey: string, name: string, owned = false) {
    if (none) return;
    if (!owned) setBusy(true);
    setNotice(null);
    try {
      for (let at = 0; at < ids.length; at += ALBUM_CHUNK) {
        const res = await apiFetch(`/api/v1/albums/${albumKey}/assets`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ asset_ids: ids.slice(at, at + ALBUM_CHUNK) }),
        });
        if (!res.ok) {
          setNotice({ text: await readDetail(res), ok: false });
          onChanged(false); // an earlier chunk may already have been added
          return;
        }
      }
      setNotice({ text: t("gallery:select.added", { count: ids.length, name }), ok: true });
      onChanged(false);
    } finally {
      if (!owned) setBusy(false);
    }
  }

  async function removeFromAlbum() {
    if (!albumId || none) return;
    setBusy(true);
    setNotice(null);
    let removed = 0;
    try {
      for (const id of ids) {
        const res = await apiFetch(`/api/v1/albums/${albumId}/assets/${id}`, { method: "DELETE" });
        if (!res.ok) {
          setNotice({ text: await readDetail(res), ok: false });
          break;
        }
        removed += 1;
      }
    } finally {
      setBusy(false);
    }
    if (removed > 0) onChanged(true);
  }

  return (
    <div className="selection-bar" role="toolbar" aria-label={t("gallery:select.label")}>
      <div className="button-row">
        <strong>{t("gallery:select.count", { count: ids.length })}</strong>
        <button type="button" className="link-button" disabled={busy} onClick={onSelectShown}>
          {t("gallery:select.all", { count: shownCount })}
        </button>
        <button type="button" className="link-button" disabled={busy || none} onClick={onClear}>
          {t("gallery:select.none")}
        </button>
        {albums && albums.length > 0 && album && (
          <>
            <select
              aria-label={t("gallery:detail.albumLabel")}
              value={album.id}
              disabled={busy}
              onChange={(e) => setTarget(e.target.value)}
            >
              {albums.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.name}
                </option>
              ))}
            </select>
            <button type="button" disabled={busy || none} onClick={() => void addToAlbum()}>
              {t("gallery:detail.addToAlbum")}
            </button>
          </>
        )}
        <input
          value={newName}
          maxLength={255}
          disabled={busy}
          placeholder={t("gallery:select.newAlbumPlaceholder")}
          aria-label={t("gallery:select.newAlbumPlaceholder")}
          onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void createAlbum();
          }}
        />
        <button type="button" disabled={busy || none || !newName.trim()} onClick={() => void createAlbum()}>
          {t("gallery:select.newAlbum")}
        </button>
        {albumId && (
          <button type="button" disabled={busy || none} onClick={() => void removeFromAlbum()}>
            {t("gallery:detail.removeFromAlbum")}
          </button>
        )}
        <button type="button" disabled={busy || none} onClick={() => setExporting((open) => !open)}>
          {t("gallery:select.export")}
        </button>
        <button type="button" disabled={busy} onClick={onDone}>
          {t("gallery:select.done")}
        </button>
      </div>
      {exporting && !none && (
        <div className="import export-box">
          <ExportStarter
            scope={{ asset_ids: ids }}
            startLabel={t("gallery:select.exportStart", { count: ids.length })}
            blocked={busy}
            onNotice={setNotice}
            onStarted={() => setExporting(false)}
          />
        </div>
      )}
      {notice && <p className={notice.ok ? "status ok" : "status fail"}>{notice.text}</p>}
    </div>
  );
}
