"use client";

import { useId } from "react";
import { useTranslation } from "react-i18next";

type Props = {
  /** true: this replaces the original server, so keep its id. false (default): a copy gets a new id. */
  keep: boolean;
  onChange: (keep: boolean) => void;
  disabled?: boolean;
};

/**
 * Admin site restore: does the restored server replace the original, or run next to it? The server cannot
 * tell, and apps use the answer to decide whether it is the same server (doc/mobile.md § Server identity).
 * The default is a new id: choosing wrongly that way costs an app one sign-in; the other way would let
 * apps merge two libraries.
 */
export function RestoreIdChoice({ keep, onChange, disabled }: Props) {
  const { t } = useTranslation(["backup"]);
  const name = useId();
  return (
    <fieldset className="import">
      <legend className="detail">{t("backup:restore.siteId.title")}</legend>
      <label className="radio-row">
        <input type="radio" name={name} checked={!keep} disabled={disabled} onChange={() => onChange(false)} />{" "}
        {t("backup:restore.siteId.new")}
      </label>
      <label className="radio-row">
        <input type="radio" name={name} checked={keep} disabled={disabled} onChange={() => onChange(true)} />{" "}
        {t("backup:restore.siteId.keep")}
      </label>
    </fieldset>
  );
}
