"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { fetchTimeZones, Me, saveTimeZone, TimeZoneInfo } from "./api";
import { applyTimeZone, timeZoneNames } from "./format";

type Props = {
  id: string;
  me: Me;
  onMe: (me: Me) => void;
};

/**
 * The server's default zone or an IANA zone from the server's list; shows times in it at once and
 * saves `user.time_zone` (null = the server's zone).
 */
export function TimeZoneSelect({ id, me, onMe }: Props) {
  const { t } = useTranslation(["settings", "common"]);
  const [error, setError] = useState<string | null>(null);
  const saved = me.time_zone ?? null;

  // The server's list and its own zone are what count; the browser's list is only for a server
  // that has none (older), and then "default" falls back to this device's zone.
  const [info, setInfo] = useState<TimeZoneInfo | null>(null);
  useEffect(() => {
    let live = true;
    void fetchTimeZones().then((got) => live && setInfo(got));
    return () => {
      live = false;
    };
  }, []);
  const names = useMemo(() => {
    if (!info) return timeZoneNames(saved);
    // A saved legacy alias (US/Pacific) is still valid, so it stays selectable.
    return saved && !info.zones.includes(saved) ? [saved, ...info.zones] : info.zones;
  }, [info, saved]);

  async function change(value: string) {
    const choice = value === "default" ? null : value;
    setError(null);
    applyTimeZone(choice ?? info?.serverZone);
    onMe({ ...me, time_zone: choice });
    try {
      onMe(await saveTimeZone(choice));
    } catch (err: unknown) {
      const detail = err instanceof Error ? err.message : t("common:requestFailed");
      setError(t("settings:timeZone.saveFailed", { detail }));
    }
  }

  const defaultLabel = info
    ? t("settings:timeZone.default", { zone: info.serverZone })
    : t("settings:timeZone.defaultUnknown");

  return (
    <>
      <select id={id} value={saved ?? "default"} onChange={(e) => void change(e.target.value)}>
        <option value="default">{defaultLabel}</option>
        {names.map((name) => (
          <option key={name} value={name}>
            {name}
          </option>
        ))}
      </select>
      {error && <p className="status fail">{error}</p>}
    </>
  );
}
