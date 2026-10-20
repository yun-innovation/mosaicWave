"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { apiFetch, assetFileUrl, readDetail } from "./api";
import { formatBytes, formatDate } from "./format";
import { Asset, isVideo, LibraryAlbum, Notice, stillSrc, thumbSrc } from "./types";
import { VideoPlayer } from "./VideoPlayer";

type Props = {
  asset: Asset;
  albums: LibraryAlbum[] | null;
  /** Album the asset was opened from; enables Remove from album. */
  albumId: string | null;
  onClose: () => void;
  /** Album membership changed; `removed` means the asset left the open album. */
  onChanged: (removed: boolean) => void;
};

export function AssetViewer({ asset, albums, albumId, onClose, onChanged }: Props) {
  const { t } = useTranslation(["gallery", "common"]);
  const [addAlbumId, setAddAlbumId] = useState("");
  const [notice, setNotice] = useState<Notice | null>(null);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const target = albums?.find((row) => row.id === addAlbumId) ?? albums?.[0] ?? null;

  async function addToAlbum() {
    if (!target) {
      setNotice({ text: t("gallery:album.createFirst"), ok: false });
      return;
    }
    const name = target.name || t("gallery:album.fallbackName");
    setNotice(null);
    const res = await apiFetch(`/api/v1/albums/${target.id}/assets`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ asset_ids: [asset.id] }),
    });
    if (!res.ok) {
      setNotice({ text: await readDetail(res), ok: false });
      return;
    }
    const body = (await res.json()) as LibraryAlbum;
    setAddAlbumId(target.id);
    setNotice({
      text:
        body.asset_count === target.asset_count
          ? t("gallery:album.alreadyIn", { name })
          : t("gallery:album.addedTo", { name }),
      ok: true,
    });
    onChanged(false);
  }

  async function removeFromAlbum() {
    if (!albumId) return;
    setNotice(null);
    const res = await apiFetch(`/api/v1/albums/${albumId}/assets/${asset.id}`, { method: "DELETE" });
    if (!res.ok) {
      setNotice({ text: await readDetail(res), ok: false });
      return;
    }
    onChanged(true);
  }

  return (
    <div className="detail-backdrop" onClick={onClose} role="presentation">
      <div
        className="detail-card"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={asset.filename}
      >
        <div className="detail-media">
          {isVideo(asset) ? (
            <VideoPlayer assetId={asset.id} poster={thumbSrc(asset)} title={asset.filename} />
          ) : (
            <img src={stillSrc(asset)} alt={asset.filename} />
          )}
        </div>
        <div className="detail-meta">
          <p className="name">{asset.filename}</p>
          <p className="detail">
            {[formatDate(asset.taken_at) ?? t("common:unknownDate"), isVideo(asset) ? null : formatBytes(asset.size)]
              .filter(Boolean)
              .join(" · ")}
          </p>
          {notice && <p className={notice.ok ? "status ok" : "status fail"}>{notice.text}</p>}
          <div className="button-row">
            <a href={assetFileUrl(asset.id, true)}>{t("gallery:detail.download")}</a>
            {albums && albums.length > 0 && target && (
              <>
                <select
                  aria-label={t("gallery:detail.albumLabel")}
                  value={target.id}
                  onChange={(e) => setAddAlbumId(e.target.value)}
                >
                  {albums.map((album) => (
                    <option key={album.id} value={album.id}>
                      {album.name}
                    </option>
                  ))}
                </select>
                <button type="button" onClick={() => void addToAlbum()}>
                  {t("gallery:detail.addToAlbum")}
                </button>
              </>
            )}
            {albumId && (
              <button type="button" onClick={() => void removeFromAlbum()}>
                {t("gallery:detail.removeFromAlbum")}
              </button>
            )}
            <button type="button" onClick={onClose}>
              {t("common:close")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
