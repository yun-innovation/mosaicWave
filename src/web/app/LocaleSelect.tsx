"use client";

import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Me, saveLocale } from "./api";
import { applyLocale, isLocale, LOCALE_NAMES, LOCALES, rememberLocale, resolveLocale, storedLocale } from "./i18n";

type Props = {
  id: string;
  me: Me;
  onMe: (me: Me) => void;
};

/** Automatic / English / 繁體中文 / 简体中文; switches at once and saves `user.locale`. */
export function LocaleSelect({ id, me, onMe }: Props) {
  const { t } = useTranslation(["settings", "common"]);
  const [error, setError] = useState<string | null>(null);

  async function change(value: string) {
    const choice = isLocale(value) ? value : null;
    setError(null);
    rememberLocale(choice);
    onMe({ ...me, locale: choice });
    await applyLocale(resolveLocale(choice));
    try {
      onMe(await saveLocale(choice));
    } catch (err: unknown) {
      const detail = err instanceof Error ? err.message : t("common:requestFailed");
      setError(t("settings:language.saveFailed", { detail }));
    }
  }

  return (
    <>
      <select
        id={id}
        value={(isLocale(me.locale) ? me.locale : storedLocale()) ?? "auto"}
        onChange={(e) => void change(e.target.value)}
      >
        <option value="auto">{t("settings:language.auto")}</option>
        {LOCALES.map((code) => (
          <option key={code} value={code} lang={code}>
            {LOCALE_NAMES[code]}
          </option>
        ))}
      </select>
      {error && <p className="status fail">{error}</p>}
    </>
  );
}
