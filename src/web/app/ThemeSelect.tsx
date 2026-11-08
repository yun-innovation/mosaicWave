"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { applyTheme, followSystemTheme, isThemeChoice, storedTheme, THEME_CHOICES, ThemeChoice } from "./theme";

type Props = {
  id: string;
};

/** Dark / Light / Follow the device. Applies at once and is remembered in this browser only. */
export function ThemeSelect({ id }: Props) {
  const { t } = useTranslation("settings");
  const [choice, setChoice] = useState<ThemeChoice>("dark");

  useEffect(() => {
    setChoice(storedTheme()); // after mount: the static page cannot know the browser's choice
    return followSystemTheme();
  }, []);

  function change(value: string) {
    if (!isThemeChoice(value)) return;
    setChoice(value);
    applyTheme(value, true);
  }

  return (
    <select id={id} value={choice} onChange={(e) => change(e.target.value)}>
      {THEME_CHOICES.map((code) => (
        <option key={code} value={code}>
          {t(`theme.${code}`)}
        </option>
      ))}
    </select>
  );
}
