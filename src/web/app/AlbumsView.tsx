"use client";

import { FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { apiFetch, readDetail } from "./api";
import { ExportStarter } from "./ExportPanel";
import { Route, routeHash } from "./hashRoute";
import { TimelineView } from "./TimelineView";
import { LibraryAlbum, Notice } from "./types";
import type { TakeoutImport } from "./useTakeoutImport";

type Props = {
  albumId: string | null;
  albums: LibraryAlbum[] | null;
  libraryRev: number;
  importer: TakeoutImport;
  go: (route: Route) => void;
  onAlbumsChanged: () => void;
  onLibraryChanged: () => void;
};

export function AlbumsView({ albumId, albums, libraryRev, importer, go, onAlbumsChanged, onLibraryChanged }: Props) {
  const { t } = useTranslation(["gallery", "backup", "common"]);
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);

  async function onCreate(event: FormEvent) {
    event.preventDefault();
    setNotice(null);
    setBusy(true);
    try {
      const res = await apiFetch("/api/v1/albums", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newName }),
      });
      if (!res.ok) {
        setNotice({ text: await readDetail(res), ok: false });
        return;
      }
      setNewName("");
      onAlbumsChanged();
    } finally {
      setBusy(false);
    }
  }

  async function onDelete(album: LibraryAlbum) {
    setNotice(null);
    setBusy(true);
    try {
      const res = await apiFetch(`/api/v1/albums/${album.id}`, { method: "DELETE" });
      if (!res.ok) {
        setNotice({ text: await readDetail(res), ok: false });
        return;
      }
      go({ view: "albums", albumId: null });
      onAlbumsChanged();
    } finally {
      setBusy(false);
    }
  }

  if (albumId) {
    const album = albums?.find((row) => row.id === albumId) ?? null;
    return (
      <section className="card gallery">
        <div className="gallery-head">
          <div className="head-title">
            <button type="button" onClick={() => go({ view: "albums", albumId: null })}>
              {t("gallery:album.back")}
            </button>
            <h2>{album ? album.name : albums ? t("gallery:album.notFound") : t("gallery:album.loading")}</h2>
          </div>
          {album && (
            <div className="button-row">
              <button type="button" disabled={busy} onClick={() => setExporting((open) => !open)}>
                {t("backup:export.album")}
              </button>
              <button type="button" disabled={busy} onClick={() => void onDelete(album)}>
                {t("gallery:deleteAlbum")}
              </button>
            </div>
          )}
        </div>
        {album && exporting && (
          <div className="import export-box">
            <ExportStarter
              scope={{ album_ids: [album.id] }}
              startLabel={t("backup:export.start")}
              blocked={importer.busy}
              onNotice={setNotice}
              onStarted={() => {
                setExporting(false);
                importer.refreshJobs();
              }}
            />
          </div>
        )}
        {notice && <p className={notice.ok ? "status ok" : "status fail"}>{notice.text}</p>}
        {album && (
          <TimelineView
            albumId={album.id}
            albums={albums}
            libraryRev={libraryRev}
            onAlbumsChanged={onAlbumsChanged}
            onLibraryChanged={onLibraryChanged}
          />
        )}
      </section>
    );
  }

  return (
    <section className="card gallery">
      <div className="gallery-head">
        <h2>{t("common:nav.albums")}</h2>
      </div>
      {notice && <p className={notice.ok ? "status ok" : "status fail"}>{notice.text}</p>}
      <form className="upload-row" onSubmit={(event) => void onCreate(event)}>
        <input
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          placeholder={t("gallery:album.newPlaceholder")}
          maxLength={255}
        />
        <button type="submit" disabled={busy || !newName.trim()}>
          {t("gallery:album.create")}
        </button>
      </form>
      {albums === null && <p className="detail">{t("gallery:album.loading")}</p>}
      {albums && albums.length === 0 && <p className="detail">{t("gallery:album.empty")}</p>}
      {albums && albums.length > 0 && (
        <div className="thumb-grid">
          {albums.map((album) => (
            <a key={album.id} className="thumb album-tile" title={album.name} href={routeHash({ view: "albums", albumId: album.id })}>
              {album.cover_asset_id ? (
                <img src={`/api/v1/assets/${album.cover_asset_id}/thumb`} alt="" loading="lazy" />
              ) : (
                <span className="album-empty">{t("gallery:album.noCover")}</span>
              )}
              <span className="album-caption">
                {t("gallery:album.caption", { name: album.name, count: album.asset_count })}
              </span>
            </a>
          ))}
        </div>
      )}
    </section>
  );
}
