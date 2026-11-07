import i18n, { activeLocale } from "./i18n";

// The zone dates and times are shown in. undefined = automatic: the device's own zone.
// Every stored time is UTC (doc/i18n.md § Time zone); the zone only matters here, when a time is shown.
let zone: string | undefined;

function usable(name: string | null | undefined): string | undefined {
  if (!name) return undefined;
  try {
    new Intl.DateTimeFormat("en", { timeZone: name });
    return name;
  } catch {
    return undefined; // a name this browser does not know: fall back to automatic
  }
}

/** The zone times are shown in, or undefined for automatic. */
export function activeTimeZone(): string | undefined {
  return zone;
}

/** The zone this device is in, shown next to "Automatic". */
export function deviceTimeZone(): string {
  return new Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

/**
 * Show times in `name` (an IANA name; null or empty = automatic). Components that format during
 * render re-render on the language event, so the screen changes at once.
 */
export function applyTimeZone(name: string | null | undefined): void {
  const next = usable(name);
  if (next === zone) return;
  zone = next;
  i18n.emit("languageChanged", i18n.language);
}

/** Zone names for a picker: what this browser knows, plus UTC and `keep` (a saved name it may not list). */
export function timeZoneNames(keep?: string | null): string[] {
  const known =
    typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : [];
  const names = new Set<string>(["UTC", ...known]);
  if (keep) names.add(keep);
  return [...names].sort((a, b) => (a === "UTC" ? -1 : b === "UTC" ? 1 : a.localeCompare(b)));
}

/** Server timestamps without an offset are UTC. */
export function parseStamp(value: string | null): Date | null {
  if (!value) return null;
  const hasTz = /Z$/i.test(value) || /[+-]\d{2}:\d{2}$/.test(value);
  const d = new Date(hasTz ? value : `${value}Z`);
  if (Number.isNaN(d.getTime())) return null;
  return d;
}

/** Stable day key (YYYY-MM-DD) in the display zone, for grouping; not for display. */
export function dayKey(value: string | null): string | null {
  const d = parseStamp(value);
  if (!d) return null;
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: zone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(d);
  const pick = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${pick("year")}-${pick("month")}-${pick("day")}`;
}

// A day key is a calendar date, not an instant: put noon UTC on it and format in UTC so no zone moves it.
function fromDayKey(key: string): Date {
  return new Date(`${key}T12:00:00Z`);
}

export function formatDay(key: string): string {
  return new Intl.DateTimeFormat(activeLocale(), { dateStyle: "medium", timeZone: "UTC" }).format(fromDayKey(key));
}

export function formatMonth(key: string): string {
  return new Intl.DateTimeFormat(activeLocale(), { year: "numeric", month: "long", timeZone: "UTC" }).format(
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
  return new Intl.DateTimeFormat(activeLocale(), { dateStyle: "short", timeStyle: "short", timeZone: zone }).format(d);
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
