"use client";

import { FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { warningText } from "./api";
import { FolderPicker } from "./FolderPicker";
import { TakeoutImport } from "./useTakeoutImport";

export function ImportView({ importer }: { importer: TakeoutImport }) {
  const { t } = useTranslation(["import", "common"]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const { busy, working, inspect } = importer;

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void importer.startImport();
  }

  return (
    <>
      <form className="card import" onSubmit={onSubmit}>
        <h2>{t("import:title")}</h2>
        <p className="detail">{t("import:intro")}</p>
        <div className="choice-col">
          <label>
            <input
              type="radio"
              name="takeout-from"
              checked={importer.from === "server"}
              onChange={() => importer.setFrom("server")}
              disabled={busy}
            />
            {t("import:from.server")}
          </label>
          <label>
            <input
              type="radio"
              name="takeout-from"
              checked={importer.from === "device"}
              onChange={() => importer.setFrom("device")}
              disabled={busy}
            />
            {t("import:from.device")}
          </label>
        </div>
        {importer.from === "server" ? (
          <>
            <label htmlFor="takeout-path">{t("import:serverPath.label")}</label>
            <div className="path-row">
              <input
                id="takeout-path"
                value={importer.path}
                onChange={(e) => importer.setPath(e.target.value)}
                placeholder={t("import:serverPath.placeholder")}
                autoComplete="off"
              />
              <button type="button" disabled={busy} onClick={() => setPickerOpen(true)}>
                {t("common:browse")}
              </button>
            </div>
          </>
        ) : (
          <>
            <label htmlFor="takeout-device">{t("import:device.label")}</label>
            <input
              id="takeout-device"
              type="file"
              multiple
              onChange={(e) => importer.setClientFiles(Array.from(e.target.files || []))}
              {...{ webkitdirectory: "", directory: "" }}
              disabled={busy}
            />
            <p className="detail">
              {importer.clientFiles.length > 0
                ? t("import:device.selected", { count: importer.clientFiles.length })
                : t("import:device.hint")}
            </p>
          </>
        )}
        <div className="button-row">
          {importer.from === "server" && (
            <button type="button" disabled={busy} onClick={() => void importer.check()}>
              {busy ? t("common:working") : t("import:check")}
            </button>
          )}
          <button type="submit" disabled={busy}>
            {busy ? t("common:working") : t("import:import")}
          </button>
          {importer.pushJobId && (
            <button type="button" disabled={working} onClick={() => void importer.cancelPush()}>
              {t("import:cancelUpload")}
            </button>
          )}
        </div>
        {importer.message && <p className="detail import-progress">{importer.message}</p>}
        {inspect && (
          <div className="inspect">
            <p className={inspect.ready ? "status ok" : "status fail"}>
              {inspect.ready ? t("import:inspect.ready") : t("import:inspect.notReady")}
            </p>
            <p className="detail">
              {[
                t("import:inspect.zips", { count: inspect.zip_count }),
                inspect.import_root ? t("import:inspect.importRoot", { path: inspect.import_root }) : null,
                inspect.album_metadata_count
                  ? t("import:inspect.albumMetadata", { count: inspect.album_metadata_count })
                  : null,
                inspect.sidecar_without_media_count
                  ? t("import:inspect.sidecars", { count: inspect.sidecar_without_media_count })
                  : null,
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
            {inspect.warnings.length > 0 && (
              <ul className="warn-list">
                {inspect.warnings.map((w, i) => (
                  <li key={`${w.code}-${i}`} className={w.severity}>
                    {warningText(w)}
                  </li>
                ))}
              </ul>
            )}
            {inspect.sidecar_without_media.length > 0 && (
              <p className="detail missing-sample">
                {t("import:inspect.missingSample", { names: inspect.sidecar_without_media.join(", ") })}
              </p>
            )}
          </div>
        )}
      </form>
      {pickerOpen && (
        <FolderPicker
          startPath={importer.path}
          title={t("import:serverPath.pickerTitle")}
          onSelect={(path) => {
            importer.setPath(path);
            setPickerOpen(false);
          }}
          onClose={() => setPickerOpen(false)}
        />
      )}
    </>
  );
}
