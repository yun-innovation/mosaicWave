"use client";

import { FormEvent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { apiFetch, readDetail, Source } from "./api";
import i18n from "./i18n";
import { isAbortError } from "./types";

type Props = {
  onClose: () => void;
  onUploaded: () => void;
};

export function UploadDialog({ onClose, onUploaded }: Props) {
  const { t } = useTranslation(["gallery", "import", "common"]);
  const [sources, setSources] = useState<Source[] | null>(null);
  const [sourceId, setSourceId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    apiFetch("/api/v1/sources", { signal: ctrl.signal })
      .then(async (res) => {
        if (!res.ok) {
          setError(await readDetail(res));
          setSources([]);
          return;
        }
        const rows = ((await res.json()) as Source[]).filter((s) => s.kind === "folder" && s.perm === "write");
        setSources(rows);
        setSourceId(rows[0]?.id ?? "");
      })
      .catch((err: unknown) => {
        if (isAbortError(err)) return;
        setError(err instanceof Error ? err.message : i18n.t("common:networkError"));
        setSources([]);
      });
    return () => ctrl.abort();
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!file || !sourceId) return;
    setBusy(true);
    setError(null);
    try {
      const data = new FormData();
      data.set("source_id", sourceId);
      data.set("file", file);
      const res = await apiFetch("/api/v1/assets", { method: "POST", body: data });
      if (!res.ok) {
        setError(await readDetail(res));
        return;
      }
      onUploaded();
      onClose();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("import:fail.upload"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="picker-backdrop" onClick={onClose} role="presentation">
      <form
        className="picker import"
        onClick={(event) => event.stopPropagation()}
        onSubmit={(event) => void onSubmit(event)}
        role="dialog"
        aria-modal="true"
        aria-labelledby="upload-title"
      >
        <h2 id="upload-title">{t("gallery:upload.title")}</h2>
        {sources === null && <p className="detail">{t("common:loading")}</p>}
        {sources && sources.length > 1 && (
          <>
            <label htmlFor="upload-source">{t("gallery:upload.sourceLabel")}</label>
            <select id="upload-source" value={sourceId} onChange={(e) => setSourceId(e.target.value)}>
              {sources.map((source) => (
                <option key={source.id} value={source.id}>
                  {t("gallery:upload.sourceOption", {
                    owner: source.owned ? t("gallery:upload.yourLibrary") : t("gallery:upload.shared"),
                    count: source.asset_count,
                  })}
                </option>
              ))}
            </select>
          </>
        )}
        {sources && sources.length > 0 && (
          <>
            <label htmlFor="upload-file">{t("gallery:upload.fileLabel")}</label>
            <input
              id="upload-file"
              type="file"
              accept="image/*,video/*"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              disabled={busy}
            />
          </>
        )}
        {sources && sources.length === 0 && !error && <p className="detail">{t("gallery:upload.noTarget")}</p>}
        {error && <p className="status fail">{error}</p>}
        <div className="button-row">
          <button type="submit" disabled={busy || !file || !sourceId}>
            {busy ? t("common:working") : t("gallery:upload.submit")}
          </button>
          <button type="button" onClick={onClose}>
            {t("common:cancel")}
          </button>
        </div>
      </form>
    </div>
  );
}
