"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { fetchTimeZones, Me, saveTimeZone, saveUseDst } from "./api";
import { applyTimeZone, observesDst, timeZoneNames } from "./format";

type Props = {
  id: string;
  me: Me;
  onMe: (me: Me) => void;
};

/**
 * An IANA zone from the server's list (UTC until one is chosen); shows times in it at once and
 * saves `user.time_zone` (null, the default, is UTC). Summer time is a separate switch, offered only
 * for a zone that has it, and off until the user turns it on.
 */
export function TimeZoneSelect({ id, me, onMe }: Props) {
  const { t } = useTranslation(["settings", "common"]);
  const [error, setError] = useState<string | null>(null);
  const saved = me.time_zone ?? null;
  const dst = me.use_dst ?? false;

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
    applyTimeZone(choice, dst);
    onMe({ ...me, time_zone: choice });
    try {
      onMe(await saveTimeZone(choice));
    } catch (err: unknown) {
      fail(err);
    }
  }

  async function changeDst(on: boolean) {
    setError(null);
    applyTimeZone(saved, on);
    onMe({ ...me, use_dst: on });
    try {
      onMe(await saveUseDst(on));
    } catch (err: unknown) {
      fail(err);
    }
  }

  function fail(err: unknown) {
    const detail = err instanceof Error ? err.message : t("common:requestFailed");
    setError(t("settings:timeZone.saveFailed", { detail }));
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
      {observesDst(saved) && (
        <label className="check-row">
          <input type="checkbox" checked={dst} onChange={(e) => void changeDst(e.target.checked)} />
          <span>{t("settings:timeZone.dst")}</span>
        </label>
      )}
      {error && <p className="status fail">{error}</p>}
    </>
  );
}
