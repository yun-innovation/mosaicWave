import i18n, { activeLocale } from "./i18n";

// The zone dates and times are shown in: the user's own, else UTC (the default).
// Every stored time is UTC (doc/i18n.md § Time zone); the zone only matters here, when a time is shown.
let zone = "UTC";
// What the user chose (zone name and summer time), as the API takes it; `zone` above is what Intl is given.
let chosen = { zone: "UTC", dst: false };

function usable(name: string | null | undefined): string {
  if (!name) return "UTC";
  try {
    new Intl.DateTimeFormat("en", { timeZone: name });
    return name;
  } catch {
    return "UTC"; // a name this browser does not know: fall back to the default
  }
}

/** The zone and summer-time choice in effect, to send to the API so it groups the way this page does. */
export function chosenTimeZone(): { zone: string; dst: boolean } {
  return chosen;
}

/** The zone times are shown in. */
export function activeTimeZone(): string {
  return zone;
}

// The UTC offset (minutes) a zone has at one instant, read from Intl's "GMT+05:30" label.
function offsetMinutes(name: string, at: Date): number | null {
  try {
    const label = new Intl.DateTimeFormat("en", { timeZone: name, timeZoneName: "longOffset" })
      .formatToParts(at)
      .find((part) => part.type === "timeZoneName")?.value;
    const m = /^GMT(?:([+-])(\d{1,2})(?::(\d{2}))?)?$/.exec(label ?? "");
    if (!m) return null;
    const minutes = m[1] ? Number(m[2]) * 60 + Number(m[3] ?? 0) : 0;
    return m[1] === "-" ? -minutes : minutes;
  } catch {
    return null;
  }
}

/** The zone's standard-time offset in minutes (the smaller of its January and July offsets), and whether it has summer time. */
function zoneOffsets(name: string): { standard: number; observesDst: boolean } | null {
  const year = new Date().getUTCFullYear();
  const jan = offsetMinutes(name, new Date(Date.UTC(year, 0, 1, 12)));
  const jul = offsetMinutes(name, new Date(Date.UTC(year, 6, 1, 12)));
  if (jan === null || jul === null) return null;
  return { standard: Math.min(jan, jul), observesDst: jan !== jul };
}

/** Does this zone move its clocks for summer time? Decides whether the setting is offered. */
export function observesDst(name: string | null | undefined): boolean {
  return !!name && (zoneOffsets(name)?.observesDst ?? false);
}

/** `+05:30` for a number of minutes, the fixed-offset zone name Intl accepts. */
function offsetName(minutes: number): string {
  const abs = Math.abs(minutes);
  const sign = minutes < 0 ? "-" : "+";
  return `${sign}${String(Math.floor(abs / 60)).padStart(2, "0")}:${String(abs % 60).padStart(2, "0")}`;
}

/**
 * Show times in `name` (an IANA name; null, empty or unknown = UTC). Without `dst` a zone that has
 * summer time is held at its standard offset all year, so the clock never jumps; with it the zone
 * follows its own rules. Components that format during render re-render on the language event, so
 * the screen changes at once.
 */
export function applyTimeZone(name: string | null | undefined, dst = false): void {
  let next = usable(name);
  const picked = { zone: next, dst };
  if (!dst) {
    const offsets = zoneOffsets(next);
    if (offsets?.observesDst) {
      const fixed = offsetName(offsets.standard);
      try {
        new Intl.DateTimeFormat("en", { timeZone: fixed });
        next = fixed; // an older browser without offset zones keeps the zone's own rules
      } catch {
        /* keep next */
      }
    }
  }
  const changed = picked.zone !== chosen.zone || picked.dst !== chosen.dst;
  chosen = picked;
  if (next === zone && !changed) return;
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

/**
 * The instant the month after `month` ("2026-03") begins in the display zone, as ISO 8601 UTC.
 * `GET /assets?before=` with it starts the timeline at the newest photo of `month`.
 */
export function monthEndInstant(month: string): string {
  const [year, number] = month.split("-").map(Number);
  return startOfDay(year, number, 1); // `number` is 1-based, so this is the first of the next month
}

/** The instant a calendar day starts in the zone this page shows; `day` may overflow ("2026-03-32" is April 1). */
function startOfDay(year: number, month: number, day: number): string {
  const utc = Date.UTC(year, month - 1, day);
  let offset = offsetMinutes(zone, new Date(utc)) ?? 0;
  offset = offsetMinutes(zone, new Date(utc - offset * 60000)) ?? offset; // a second look settles a switch of summer time
  return new Date(utc - offset * 60000).toISOString();
}

/** A search date range from two calendar days ("2026-03-01"; either may be empty) as instants, end exclusive. */
export function dateRangeInstants(from: string, to: string): { from: string | null; to: string | null } {
  const parts = (value: string) => value.split("-").map(Number);
  const [fy, fm, fd] = parts(from);
  const [ty, tm, td] = parts(to);
  return {
    from: from ? startOfDay(fy, fm, fd) : null,
    to: to ? startOfDay(ty, tm, td + 1) : null, // the whole last day counts
  };
}

/** "March 2026" for a month key ("2026-03"). */
export function formatMonthKey(month: string): string {
  return formatMonth(`${month}-01`);
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
