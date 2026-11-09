"use client";

import { KeyboardEvent, PointerEvent, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { formatMonthKey } from "./format";
import type { MonthsList } from "./types";

export const UNDATED = "unknown";

type Props = {
  list: MonthsList;
  /** The month ("2026-03") or UNDATED the top of the page is in; null when not known yet. */
  active: string | null;
  /** The user picked this month (or UNDATED): load the timeline from there. */
  onJump: (key: string) => void;
};

/**
 * A rail down the right edge that stands for the whole timeline, newest at the top. Press or drag on
 * it to see a date and let go to jump there; arrow keys, Home and End do the same. Years are labelled.
 */
export function TimelineScrubber({ list, active, onJump }: Props) {
  const { t } = useTranslation(["gallery", "common"]);
  const keys = [...list.months.map((m) => m.month), ...(list.undated > 0 ? [UNDATED] : [])];
  const [hover, setHover] = useState<number | null>(null);
  const track = useRef<HTMLDivElement>(null);

  if (keys.length < 2) return null; // nothing to jump between

  const label = (key: string) => (key === UNDATED ? t("common:unknownDate") : formatMonthKey(key));
  const place = (index: number) => (index / (keys.length - 1)) * 100;
  const activeIndex = active ? keys.indexOf(active) : -1;

  function indexAt(event: PointerEvent): number {
    const box = track.current?.getBoundingClientRect();
    if (!box || box.height === 0) return 0;
    const ratio = Math.min(1, Math.max(0, (event.clientY - box.top) / box.height));
    return Math.round(ratio * (keys.length - 1));
  }

  function onKey(event: KeyboardEvent) {
    const from = activeIndex < 0 ? 0 : activeIndex;
    const to =
      event.key === "ArrowDown" || event.key === "PageDown"
        ? from + 1
        : event.key === "ArrowUp" || event.key === "PageUp"
          ? from - 1
          : event.key === "Home"
            ? 0
            : event.key === "End"
              ? keys.length - 1
              : null;
    if (to === null) return;
    event.preventDefault();
    onJump(keys[Math.min(keys.length - 1, Math.max(0, to))]);
  }

  // The first month of each year, newest first, carries that year's label.
  const years = keys.flatMap((key, index) => {
    const year = key === UNDATED ? null : key.slice(0, 4);
    const before = index > 0 ? keys[index - 1] : null;
    return year && (before === null || before === UNDATED || before.slice(0, 4) !== year) ? [{ year, index }] : [];
  });

  return (
    <div
      ref={track}
      className="scrubber"
      role="slider"
      tabIndex={0}
      aria-label={t("gallery:scrubber.label")}
      aria-orientation="vertical"
      aria-valuemin={0}
      aria-valuemax={keys.length - 1}
      aria-valuenow={Math.max(0, activeIndex)}
      aria-valuetext={active ? label(active) : undefined}
      onKeyDown={onKey}
      onPointerDown={(event) => {
        event.currentTarget.setPointerCapture(event.pointerId);
        setHover(indexAt(event));
      }}
      onPointerMove={(event) => {
        if (event.currentTarget.hasPointerCapture(event.pointerId)) setHover(indexAt(event));
      }}
      onPointerUp={(event) => {
        const index = indexAt(event);
        setHover(null);
        onJump(keys[index]);
      }}
      onPointerCancel={() => setHover(null)}
    >
      <div className="scrubber-line" />
      {years.map(({ year, index }) => (
        <span key={year} className="scrubber-year" style={{ top: `${place(index)}%` }}>
          {year}
        </span>
      ))}
      {activeIndex >= 0 && <span className="scrubber-thumb" style={{ top: `${place(activeIndex)}%` }} />}
      {hover !== null && (
        <span className="scrubber-tip" style={{ top: `${place(hover)}%` }}>
          {label(keys[hover])}
        </span>
      )}
    </div>
  );
}
