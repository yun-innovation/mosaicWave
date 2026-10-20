"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { apiFetch, readDetail } from "./api";
import { AssetViewer } from "./AssetViewer";
import { dayKey, formatDay, formatMonth } from "./format";
import i18n from "./i18n";
import { Asset, AssetList, ignoreAbort, isAbortError, LibraryAlbum, thumbSrc } from "./types";

const UNKNOWN_DAY = "unknown";

type TimelineBlock = { key: string; month: string | null; day: string; items: Asset[] };

function timelineBlocks(assets: Asset[]): TimelineBlock[] {
  const groups = new Map<string, Asset[]>();
  for (const asset of assets) {
    const key = dayKey(asset.taken_at) ?? UNKNOWN_DAY;
    const items = groups.get(key);
    if (items) items.push(asset);
    else groups.set(key, [asset]);
  }
  const unknown = i18n.t("common:unknownDate");
  let lastMonth = "";
  return [...groups].map(([day, items]) => {
    const known = day !== UNKNOWN_DAY;
    const month = known ? formatMonth(day) : unknown;
    const heading = month !== lastMonth ? month : null;
    lastMonth = month;
    return { key: day, month: heading, day: known ? formatDay(day) : unknown, items };
  });
}

type Props = {
  /** Only this album's members; null for the whole library. */
  albumId: string | null;
  albums: LibraryAlbum[] | null;
  libraryRev: number;
  onAlbumsChanged: () => void;
  onLibraryChanged: () => void;
};

export function TimelineView({ albumId, albums, libraryRev, onAlbumsChanged, onLibraryChanged }: Props) {
  const { t } = useTranslation(["gallery", "common"]);
  const [assets, setAssets] = useState<Asset[] | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [selected, setSelected] = useState<Asset | null>(null);

  const load = useCallback(
    async (signal?: AbortSignal, cursor?: string | null) => {
      if (!cursor) setError(null);
      try {
        const qs = new URLSearchParams({ limit: "50" });
        if (cursor) qs.set("cursor", cursor);
        if (albumId) qs.set("album_id", albumId);
        const res = await apiFetch(`/api/v1/assets?${qs}`, { signal });
        if (!res.ok) {
          if (!cursor) setAssets(null);
          setError(await readDetail(res));
          return;
        }
        const body = (await res.json()) as AssetList;
        setAssets((prev) => (cursor && prev ? [...prev, ...body.items] : body.items));
        setNextCursor(body.next_cursor);
      } catch (err: unknown) {
        if (isAbortError(err)) return;
        if (!cursor) setAssets(null);
        setError(err instanceof Error ? err.message : i18n.t("common:networkError"));
      }
    },
    [albumId],
  );

  useEffect(() => {
    const ctrl = new AbortController();
    ignoreAbort(load(ctrl.signal));
    return () => ctrl.abort();
  }, [load, libraryRev]);

  async function loadMore() {
    setLoadingMore(true);
    try {
      await load(undefined, nextCursor);
    } finally {
      setLoadingMore(false);
    }
  }

  const closeViewer = useCallback(() => setSelected(null), []);

  return (
    <>
      {error && <p className="status fail">{error}</p>}
      {!error && assets === null && <p className="detail">{t("gallery:loadingLibrary")}</p>}
      {!error && assets && assets.length === 0 && (
        <p className="detail">{albumId ? t("gallery:emptyAlbum") : t("gallery:emptyLibrary")}</p>
      )}
      {!error && assets && assets.length > 0 && (
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
                    <img src={thumbSrc(asset)} alt={asset.filename} loading="lazy" />
                  </button>
                ))}
              </div>
            </div>
          ))}
          {nextCursor && (
            <button type="button" className="load-more" disabled={loadingMore} onClick={() => void loadMore()}>
              {t("gallery:loadMore")}
            </button>
          )}
        </>
      )}
      {selected && (
        <AssetViewer
          asset={selected}
          albums={albums}
          albumId={albumId}
          onClose={closeViewer}
          onChanged={(removed) => {
            if (removed) {
              setSelected(null);
              onLibraryChanged();
            } else {
              onAlbumsChanged();
            }
          }}
        />
      )}
    </>
  );
}
