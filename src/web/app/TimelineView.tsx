"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { apiFetch, readDetail } from "./api";
import { AssetViewer } from "./AssetViewer";
import { chosenTimeZone, dayKey, formatDay, formatMonth, formatMonthKey, monthEndInstant, dateRangeInstants } from "./format";
import i18n from "./i18n";
import { SelectionBar } from "./SelectionBar";
import { TimelineScrubber, UNDATED } from "./TimelineScrubber";
import { Asset, AssetList, ignoreAbort, isAbortError, LibraryAlbum, MonthsList, thumbSrc } from "./types";

// Before every real date: a list started here holds only the photos with no date.
const BEFORE_ALL = "0001-01-01T00:00:00Z";

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
  // Set by the scrubber: the list starts at this month instead of the newest photo.
  const [anchor, setAnchor] = useState<{ month: string; before: string } | null>(null);
  const [months, setMonths] = useState<MonthsList | null>(null);
  const [activeMonth, setActiveMonth] = useState<string | null>(null);
  // What the search row shows (draft) and what the list is using (applied, set on Search).
  const [draft, setDraft] = useState({ q: "", from: "", to: "" });
  const [applied, setApplied] = useState({ q: "", from: "", to: "" });
  // Select mode: a click picks a photo instead of opening it. Picks keep the order they were made in.
  const [selecting, setSelecting] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const top = useRef<HTMLDivElement>(null);
  const searching = Boolean(applied.q || applied.from || applied.to);
  const before = anchor?.before ?? null;
  const zone = chosenTimeZone(); // changes re-render this view: applyTimeZone emits the language event

  useEffect(() => {
    setAnchor(null);
    setDraft({ q: "", from: "", to: "" });
    setApplied({ q: "", from: "", to: "" });
    setSelecting(false);
    setPicked([]);
  }, [albumId]);

  // A new search shows different photos, so picks made on the old list would be hidden ones.
  useEffect(() => setPicked([]), [applied]);

  const togglePick = (ids: string[]) =>
    setPicked((prev) => {
      const all = ids.every((id) => prev.includes(id));
      return all ? prev.filter((id) => !ids.includes(id)) : [...prev, ...ids.filter((id) => !prev.includes(id))];
    });

  const load = useCallback(
    async (signal?: AbortSignal, cursor?: string | null) => {
      if (!cursor) setError(null);
      try {
        const qs = new URLSearchParams({ limit: "50" });
        if (cursor) qs.set("cursor", cursor);
        if (albumId) qs.set("album_id", albumId);
        if (before && !cursor) qs.set("before", before);
        if (applied.q.trim()) qs.set("q", applied.q.trim());
        const range = dateRangeInstants(applied.from, applied.to);
        if (range.from) qs.set("taken_from", range.from);
        if (range.to) qs.set("taken_to", range.to);
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
    // `zone` is a dependency because a date range means different instants in another zone.
    [albumId, before, applied, zone.zone, zone.dst],
  );

  useEffect(() => {
    const ctrl = new AbortController();
    // Group in the zone this page shows days in. Sent explicitly, so a zone just picked in Settings
    // counts before its save reaches the account.
    const qs = new URLSearchParams({ time_zone: zone.zone, use_dst: String(zone.dst) });
    if (albumId) qs.set("album_id", albumId);
    apiFetch(`/api/v1/assets/months?${qs}`, { signal: ctrl.signal })
      .then(async (res) => setMonths(res.ok ? ((await res.json()) as MonthsList) : null))
      .catch(() => setMonths(null)); // no scrubber is better than an error: the list still works
    return () => ctrl.abort();
  }, [albumId, libraryRev, zone.zone, zone.dst]);

  // Which month the top of the page is in, so the scrubber can show where you are.
  const shown = assets ? `${assets[0]?.id ?? ""}:${assets.length}` : "";
  useEffect(() => {
    if (!shown || typeof IntersectionObserver === "undefined") return;
    const blocks = [...document.querySelectorAll<HTMLElement>(".day-block[data-day]")];
    const inView = new Set<HTMLElement>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) inView.add(entry.target as HTMLElement);
          else inView.delete(entry.target as HTMLElement);
        }
        const first = blocks.find((block) => inView.has(block));
        const day = first?.dataset.day;
        if (day) setActiveMonth(day === UNKNOWN_DAY ? UNDATED : day.slice(0, 7));
      },
      { rootMargin: "-15% 0px -80% 0px" },
    );
    blocks.forEach((block) => observer.observe(block));
    return () => observer.disconnect();
  }, [shown]);

  function runSearch(event: React.FormEvent) {
    event.preventDefault();
    setAnchor(null);
    setApplied({ ...draft });
  }

  function clearSearch() {
    const empty = { q: "", from: "", to: "" };
    setDraft(empty);
    setApplied(empty);
  }

  function jumpTo(key: string) {
    const next = key === UNDATED ? { month: key, before: BEFORE_ALL } : { month: key, before: monthEndInstant(key) };
    setAnchor(next);
    setActiveMonth(key);
    top.current?.scrollIntoView({ block: "start" });
  }

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
    <div ref={top} className={months && !searching ? "timeline with-scrubber" : "timeline"}>
      <form className="search-row" role="search" onSubmit={runSearch}>
        <input
          type="search"
          value={draft.q}
          maxLength={200}
          placeholder={t("gallery:search.placeholder")}
          aria-label={t("gallery:search.placeholder")}
          onChange={(e) => setDraft({ ...draft, q: e.target.value })}
        />
        <label>
          {t("gallery:search.from")}
          <input
            type="date"
            value={draft.from}
            max={draft.to || undefined}
            onChange={(e) => setDraft({ ...draft, from: e.target.value })}
          />
        </label>
        <label>
          {t("gallery:search.to")}
          <input
            type="date"
            value={draft.to}
            min={draft.from || undefined}
            onChange={(e) => setDraft({ ...draft, to: e.target.value })}
          />
        </label>
        <button type="submit">{t("gallery:search.submit")}</button>
        {(searching || draft.q || draft.from || draft.to) && (
          <button type="button" className="link-button" onClick={clearSearch}>
            {t("gallery:search.clear")}
          </button>
        )}
      </form>
      {months && !searching && <TimelineScrubber list={months} active={activeMonth} onJump={jumpTo} />}
      {anchor && !searching && (
        <p className="timeline-anchor">
          {t("gallery:scrubber.from", {
            when: anchor.month === UNDATED ? t("common:unknownDate") : formatMonthKey(anchor.month),
          })}{" "}
          <button type="button" className="link-button" onClick={() => setAnchor(null)}>
            {t("gallery:scrubber.latest")}
          </button>
        </p>
      )}
      {assets && assets.length > 0 && !selecting && (
        <div className="select-toggle">
          <button type="button" onClick={() => setSelecting(true)}>
            {t("gallery:select.start")}
          </button>
        </div>
      )}
      {selecting && (
        <SelectionBar
          ids={picked}
          shownCount={assets?.length ?? 0}
          albums={albums}
          albumId={albumId}
          onSelectShown={() => setPicked((assets ?? []).map((asset) => asset.id))}
          onClear={() => setPicked([])}
          onDone={() => {
            setSelecting(false);
            setPicked([]);
          }}
          onChanged={(removed) => {
            if (removed) {
              setPicked([]);
              onLibraryChanged();
            } else {
              onAlbumsChanged();
            }
          }}
        />
      )}
      {error && <p className="status fail">{error}</p>}
      {!error && assets === null && <p className="detail">{t("gallery:loadingLibrary")}</p>}
      {!error && assets && assets.length === 0 && (
        <p className="detail">
          {searching ? t("gallery:search.none") : albumId ? t("gallery:emptyAlbum") : t("gallery:emptyLibrary")}
        </p>
      )}
      {!error && assets && assets.length > 0 && (
        <>
          {timelineBlocks(assets).map((group) => (
            <div key={group.key} className="day-block" data-day={group.key}>
              {group.month && <h3 className="timeline-month">{group.month}</h3>}
              <h4 className="timeline-day">
                {group.day}
                {selecting && (
                  <button
                    type="button"
                    className="link-button"
                    onClick={() => togglePick(group.items.map((asset) => asset.id))}
                  >
                    {group.items.every((asset) => picked.includes(asset.id))
                      ? t("gallery:select.unpickDay")
                      : t("gallery:select.pickDay")}
                  </button>
                )}
              </h4>
              <div className="thumb-grid">
                {group.items.map((asset) => (
                  <button
                    type="button"
                    key={asset.id}
                    className={selecting && picked.includes(asset.id) ? "thumb picked" : "thumb"}
                    aria-pressed={selecting ? picked.includes(asset.id) : undefined}
                    onClick={() => (selecting ? togglePick([asset.id]) : setSelected(asset))}
                    title={asset.filename}
                  >
                    <img src={thumbSrc(asset)} alt={asset.filename} loading="lazy" />
                    {selecting && <span className="pick-mark" aria-hidden="true">{picked.includes(asset.id) ? "✓" : ""}</span>}
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
    </div>
  );
}
