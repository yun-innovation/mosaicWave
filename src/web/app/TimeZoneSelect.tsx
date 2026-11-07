"use client";

import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { Me, saveTimeZone } from "./api";
import { applyTimeZone, deviceTimeZone, timeZoneNames } from "./format";

type Props = {
  id: string;
  me: Me;
  onMe: (me: Me) => void;
};

/** Automatic (this device) or an IANA zone; shows times in it at once and saves `user.time_zone`. */
export function TimeZoneSelect({ id, me, onMe }: Props) {
  const { t } = useTranslation(["settings", "common"]);
  const [error, setError] = useState<string | null>(null);
  const saved = me.time_zone ?? null;
  const names = useMemo(() => timeZoneNames(saved), [saved]);

  async function change(value: string) {
    const choice = value === "auto" ? null : value;
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
      <select id={id} value={saved ?? "auto"} onChange={(e) => void change(e.target.value)}>
        <option value="auto">{`${t("settings:timeZone.auto")} · ${deviceTimeZone()}`}</option>
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
