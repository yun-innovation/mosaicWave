"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { DEFAULT_PAGE_SIZE, PAGE_SIZES, savePageSize, storedPageSize } from "./pageSize";

type Props = {
  id: string;
};

/** How many photos the gallery loads at a time. Remembered in this browser only. */
export function PageSizeSelect({ id }: Props) {
  const { t } = useTranslation("settings");
  const [size, setSize] = useState(DEFAULT_PAGE_SIZE);

  useEffect(() => {
    setSize(storedPageSize()); // after mount: the static page cannot know the browser's choice
  }, []);

  function change(value: string) {
    const next = Number(value);
    if (!PAGE_SIZES.includes(next)) return;
    setSize(next);
    savePageSize(next);
  }

  return (
    <select id={id} value={size} onChange={(e) => change(e.target.value)}>
      {PAGE_SIZES.map((count) => (
        <option key={count} value={count}>
          {t("pageSize.option", { count })}
        </option>
      ))}
    </select>
  );
}
