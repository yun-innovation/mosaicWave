"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { apiFetch, HostSettings, readDetail } from "./api";

/**
 * Admin Server page, Server identity: the id apps use to recognise this library, and the action that
 * gives a hand-copied database a new one. Design: doc/mobile.md § Server identity.
 */
export function ServerIdSection() {
  const { t } = useTranslation(["settings", "common"]);
  const [ids, setIds] = useState<Pick<HostSettings, "server_id" | "data_epoch" | "instance_id"> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await apiFetch("/api/v1/settings");
      if (!res.ok) {
        setError(await readDetail(res));
        return;
      }
      setIds((await res.json()) as HostSettings);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("settings:loadFailed"));
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  async function reset() {
    if (!window.confirm(t("settings:serverId.confirm"))) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const res = await apiFetch("/api/v1/site/id/reset", { method: "POST" });
      if (!res.ok) {
        setError(await readDetail(res));
        return;
      }
      await load();
      setNotice(t("settings:serverId.done"));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("settings:serverId.failed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="import">
      <h3 className="section-title divided">{t("settings:serverId.title")}</h3>
      <p className="detail">{t("settings:serverId.help")}</p>
      {error && <p className="status fail">{error}</p>}
      {notice && !error && <p className="detail">{notice}</p>}
      {ids && (
        <>
          <p className="detail">{t("settings:serverId.serverId", { id: ids.server_id })}</p>
          <p className="detail">{t("settings:serverId.epoch", { epoch: ids.data_epoch })}</p>
          <p className="detail">{t("settings:serverId.instance", { id: ids.instance_id })}</p>
          <p className="detail">{t("settings:serverId.resetHelp")}</p>
          <div className="button-row">
            <button type="button" disabled={busy} onClick={() => void reset()}>
              {busy ? t("common:working") : t("settings:serverId.reset")}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
