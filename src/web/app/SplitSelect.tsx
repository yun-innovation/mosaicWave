"use client";

import { useId } from "react";
import { useTranslation } from "react-i18next";

/** Part sizes offered for a backup, in GB. Empty: one zip, however big. */
const SIZES = ["", "2", "4", "10", "50"];

/**
 * Choose how big the zips of a backup may be. A library or site that is bigger is written as several
 * zips that belong together; restoring needs every one of them.
 */
export function SplitSelect({
  value,
  onChange,
  disabled,
}: {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation("backup");
  const id = useId();
  return (
    <div className="export-options">
      <label htmlFor={id}>{t("split.label")}</label>
      <select id={id} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
        {SIZES.map((size) => (
          <option key={size || "one"} value={size}>
            {size ? t("split.gb", { count: Number(size) }) : t("split.one")}
          </option>
        ))}
      </select>
      <p className="detail">{t("split.help")}</p>
    </div>
  );
}
