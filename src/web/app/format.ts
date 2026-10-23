import { activeLocale } from "./i18n";

/** Server timestamps without an offset are UTC. */
export function parseStamp(value: string | null): Date | null {
  if (!value) return null;
  const hasTz = /Z$/i.test(value) || /[+-]\d{2}:\d{2}$/.test(value);
  const d = new Date(hasTz ? value : `${value}Z`);
  if (Number.isNaN(d.getTime())) return null;
  return d;
}

/** Stable local-day key (YYYY-MM-DD) for grouping; not for display. */
export function dayKey(value: string | null): string | null {
  const d = parseStamp(value);
  if (!d) return null;
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

function fromDayKey(key: string): Date {
  return new Date(`${key}T12:00:00`);
}

export function formatDay(key: string): string {
  return new Intl.DateTimeFormat(activeLocale(), { dateStyle: "medium" }).format(fromDayKey(key));
}

export function formatMonth(key: string): string {
  return new Intl.DateTimeFormat(activeLocale(), { year: "numeric", month: "long" }).format(
    fromDayKey(key),
  );
}

export function formatDate(value: string | null): string | null {
  const key = dayKey(value);
  return key ? formatDay(key) : null;
}

export function formatDateTime(value: string | null): string {
  const d = parseStamp(value);
  if (!d) return value || "";
  return new Intl.DateTimeFormat(activeLocale(), { dateStyle: "short", timeStyle: "short" }).format(d);
}

export function formatNumber(value: number): string {
  return new Intl.NumberFormat(activeLocale()).format(value);
}

export function formatBytes(size: number): string {
  const locale = activeLocale();
  if (size < 1024) {
    return new Intl.NumberFormat(locale, { style: "unit", unit: "byte", unitDisplay: "narrow" }).format(size);
  }
  const [value, unit] =
    size < 1024 * 1024
      ? [size / 1024, "kilobyte"]
      : size < 1024 * 1024 * 1024
        ? [size / (1024 * 1024), "megabyte"]
        : [size / (1024 * 1024 * 1024), "gigabyte"];
  return new Intl.NumberFormat(locale, {
    style: "unit",
    unit,
    unitDisplay: "short",
    maximumFractionDigits: 1,
    minimumFractionDigits: 1,
  }).format(value);
}
