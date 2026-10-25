"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { apiFetch, Me, readDetail, Source } from "./api";
import { ExportPanel } from "./ExportPanel";
import i18n from "./i18n";
import { LibraryBackupPanel } from "./LibraryBackupPanel";
import { LocaleSelect } from "./LocaleSelect";
import type { TakeoutImport } from "./useTakeoutImport";

type Props = {
  me: Me;
  importer: TakeoutImport;
  onMe: (me: Me) => void;
};

/**
 * Personal options, the same for everyone including admins: language, thumbnails, backing up your
 * own library and exporting your photos as ordinary files. Options for the whole server (folders, people) are on the admin Server page.
 */
export function SettingsPanel({ me, importer, onMe }: Props) {
  const { t } = useTranslation(["settings", "common", "gallery"]);
  const [sources, setSources] = useState<Source[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [thumbsError, setThumbsError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const srcRes = await apiFetch("/api/v1/sources");
      if (!srcRes.ok) {
        const detail = await readDetail(srcRes);
        setSources([]);
        setError(srcRes.status === 401 ? i18n.t("settings:signInAgain", { detail }) : detail);
        return;
      }
      setSources((await srcRes.json()) as Source[]);
    } catch (err: unknown) {
      setSources((prev) => prev ?? []);
      setError(err instanceof Error ? err.message : i18n.t("settings:loadFailed"));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const library = sources?.find((row) => row.owned) ?? sources?.[0];
  return (
    <section className="card settings personal-settings">
      <div className="gallery-head">
        <h2>{t("settings:title")}</h2>
      </div>
      <div className="import">
        <label htmlFor="ui_locale" className="section-title">
          {t("settings:language.label")}
        </label>
        <p className="detail">{t("settings:language.help")}</p>
        <LocaleSelect id="ui_locale" me={me} onMe={onMe} />
      </div>
      <div className="import">
        <h3 className="section-title">{t("settings:thumbnails.label")}</h3>
        <p className="detail">{t("settings:thumbnails.help")}</p>
        <div className="button-row">
          <button
            type="button"
            disabled={importer.busy}
            onClick={() => {
              setThumbsError(null);
              void importer.generateThumbs().then(setThumbsError);
            }}
          >
            {importer.busy ? t("common:working") : t("gallery:generateThumbs")}
          </button>
        </div>
        {thumbsError && <p className="status fail">{thumbsError}</p>}
      </div>
      <LibraryBackupPanel importer={importer} isAdmin={me.role === "admin"} />
      <ExportPanel importer={importer} />
      {error && <p className="status fail">{error}</p>}
      {sources === null && !error && <p className="detail">{t("common:loading")}</p>}
      {library && (
        <p className="detail">
          {t("settings:library.summary", { count: library.asset_count })}
          {library.error ? ` · ${library.error}` : ""}
        </p>
      )}
    </section>
  );
}
