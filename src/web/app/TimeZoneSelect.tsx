"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { fetchTimeZones, Me, saveTimeZone } from "./api";
import { applyTimeZone, timeZoneNames } from "./format";

type Props = {
  id: string;
  me: Me;
  onMe: (me: Me) => void;
};

/**
 * An IANA zone from the server's list (UTC until one is chosen); shows times in it at once and
 * saves `user.time_zone` (null, the default, is UTC).
 */
export function TimeZoneSelect({ id, me, onMe }: Props) {
  const { t } = useTranslation(["settings", "common"]);
  const [error, setError] = useState<string | null>(null);
  const saved = me.time_zone ?? null;

  // The server's list is what counts; the browser's is only for a server that has none (older).
  const [served, setServed] = useState<string[] | null>(null);
  useEffect(() => {
    let live = true;
    void fetchTimeZones().then((zones) => live && setServed(zones));
    return () => {
      live = false;
    };
  }, []);
  const names = useMemo(() => {
    if (!served) return timeZoneNames(saved);
    // A saved legacy alias (US/Pacific) is still valid, so it stays selectable.
    return saved && !served.includes(saved) ? [saved, ...served] : served;
  }, [served, saved]);

  async function change(choice: string) {
    setError(null);
    applyTimeZone(choice);
    onMe({ ...me, time_zone: choice });
    try {
      onMe(await saveTimeZone(choice));
    } catch (err: unknown) {
      const detail = err instanceof Error ? err.message : t("common:requestFailed");
      setError(t("settings:timeZone.saveFailed", { detail }));
    }
  }

  return (
    <>
      <select id={id} value={saved ?? "UTC"} onChange={(e) => void change(e.target.value)}>
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
