"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { apiFetch, errorText } from "./api";
import { formatDateTime } from "./format";
import i18n from "./i18n";
import { MaintenanceStatus, Notice } from "./types";

type Props = {
  status: MaintenanceStatus;
  onChanged: (status: MaintenanceStatus) => void;
};

/** Admin: the maintenance switch and the message shown to everyone else. */
export function MaintenanceView({ status, onChanged }: Props) {
  const { t } = useTranslation(["maintenance", "common"]);
  const [reason, setReason] = useState(status.reason);
  const [working, setWorking] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const on = status.state !== "off";

  // Follow a change made elsewhere (another admin, the command line).
  useEffect(() => {
    setReason(status.reason);
  }, [status.reason, on]);

  async function send(enabled: boolean) {
    setConfirming(false);
    setWorking(true);
    setNotice(null);
    try {
      const res = await apiFetch("/api/v1/maintenance", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled, reason }),
      });
      const body: unknown = await res.json().catch(() => null);
      if (!res.ok) {
        setNotice({ text: errorText(body, res.status), ok: false });
        return;
      }
      onChanged(body as MaintenanceStatus);
      setNotice({ text: i18n.t("maintenance:saved"), ok: true });
    } catch (err: unknown) {
      setNotice({ text: err instanceof Error ? err.message : i18n.t("common:networkError"), ok: false });
    } finally {
      setWorking(false);
    }
  }

  return (
    <section className="card maintenance">
      <div className="gallery-head">
        <h2>{t("maintenance:title")}</h2>
      </div>
      <div className="import">
        <p className="detail">{t("maintenance:intro")}</p>
        <p className={on ? "status fail" : "detail"} role="status">
          {t(`maintenance:state.${status.state}`)}
          {on && status.since ? ` ${t("maintenance:since", { time: formatDateTime(status.since) })}` : ""}
        </p>
        <label htmlFor="maintenance-reason">{t("maintenance:reasonLabel")}</label>
        <input
          id="maintenance-reason"
          value={reason}
          maxLength={500}
          placeholder={t("maintenance:reasonPlaceholder")}
          disabled={working}
          onChange={(event) => setReason(event.target.value)}
        />
        <div className="button-row">
          {on ? (
            <>
              <button type="button" disabled={working || reason.trim() === status.reason} onClick={() => void send(true)}>
                {t("maintenance:update")}
              </button>
              <button type="button" disabled={working} onClick={() => void send(false)}>
                {working ? t("common:working") : t("maintenance:turnOff")}
              </button>
            </>
          ) : confirming ? (
            <>
              <span className="detail">{t("maintenance:confirmOn")}</span>
              <button type="button" disabled={working} onClick={() => void send(true)}>
                {t("maintenance:confirmYes")}
              </button>
              <button type="button" onClick={() => setConfirming(false)}>
                {t("common:cancel")}
              </button>
            </>
          ) : (
            <button type="button" disabled={working} onClick={() => setConfirming(true)}>
              {t("maintenance:turnOn")}
            </button>
          )}
        </div>
        {notice && <p className={notice.ok ? "detail import-progress" : "status fail"}>{notice.text}</p>}
        <p className="detail">{t("maintenance:cli")}</p>
      </div>
    </section>
  );
}
