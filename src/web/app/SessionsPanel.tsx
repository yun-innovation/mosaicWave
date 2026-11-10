"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { apiFetch, readDetail } from "./api";
import { formatDateTime } from "./format";
import { ignoreAbort, Notice } from "./types";

type SessionRow = {
  id: string;
  kind: "browser" | "app";
  device_name: string | null;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  current: boolean;
};

type Lifetimes = { browser_days: number; app_days: number };

/** Your signed-in browsers and apps, with Sign out; for an admin also how long a new sign-in lasts. */
export function SessionsPanel({ isAdmin }: { isAdmin: boolean }) {
  const { t } = useTranslation(["settings", "common"]);
  const [rows, setRows] = useState<SessionRow[] | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState(false);
  const [days, setDays] = useState<{ browser: string; app: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await apiFetch("/api/v1/me/sessions");
      if (!res.ok) {
        setNotice({ text: await readDetail(res), ok: false });
        setRows([]);
        return;
      }
      setRows((await res.json()) as SessionRow[]);
    } catch (err: unknown) {
      setRows((prev) => prev ?? []);
      setNotice({ text: err instanceof Error ? err.message : t("common:networkError"), ok: false });
    }
  }, [t]);

  useEffect(() => {
    ignoreAbort(load());
  }, [load]);

  useEffect(() => {
    if (!isAdmin) return;
    void apiFetch("/api/v1/settings/sessions")
      .then(async (res) => {
        if (!res.ok) return;
        const body = (await res.json()) as Lifetimes;
        setDays({ browser: String(body.browser_days), app: String(body.app_days) });
      })
      .catch(() => undefined);
  }, [isAdmin]);

  async function signOut(row: SessionRow) {
    setBusy(true);
    setNotice(null);
    try {
      const res = await apiFetch(`/api/v1/me/sessions/${row.id}`, { method: "DELETE" });
      if (!res.ok) {
        setNotice({ text: await readDetail(res), ok: false });
        return;
      }
      if (row.current) window.location.reload(); // this session is over: the page shows the sign-in again
      else await load();
    } finally {
      setBusy(false);
    }
  }

  async function signOutOthers() {
    setBusy(true);
    setNotice(null);
    try {
      const res = await apiFetch("/api/v1/me/sessions/revoke-others", { method: "POST" });
      if (!res.ok) {
        setNotice({ text: await readDetail(res), ok: false });
        return;
      }
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function saveDays() {
    if (!days) return;
    setBusy(true);
    setNotice(null);
    try {
      const res = await apiFetch("/api/v1/settings/sessions", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ browser_days: Number(days.browser), app_days: Number(days.app) }),
      });
      setNotice(
        res.ok ? { text: t("settings:sessions.saved"), ok: true } : { text: await readDetail(res), ok: false },
      );
    } finally {
      setBusy(false);
    }
  }

  const others = rows?.filter((row) => !row.current).length ?? 0;
  return (
    <div className="import">
      <h3 className="section-title divided">{t("settings:sessions.title")}</h3>
      <p className="detail">{t("settings:sessions.help")}</p>
      {rows === null && <p className="detail">{t("common:loading")}</p>}
      {rows && (
        <ul className="backup-list">
          {rows.map((row) => (
            <li key={row.id}>
              <div className="backup-meta">
                <span>
                  {row.device_name || t(`settings:sessions.kind.${row.kind}`)}
                  {row.current ? ` · ${t("settings:sessions.thisDevice")}` : ""}
                </span>
                <p className="detail">
                  {[
                    t(`settings:sessions.kind.${row.kind}`),
                    t("settings:sessions.lastSeen", { when: formatDateTime(row.last_seen_at) }),
                    t("settings:sessions.expires", { when: formatDateTime(row.expires_at) }),
                  ].join(" · ")}
                </p>
              </div>
              <button type="button" disabled={busy} onClick={() => void signOut(row)}>
                {t("settings:sessions.signOut")}
              </button>
            </li>
          ))}
        </ul>
      )}
      {others > 0 && (
        <div className="button-row">
          <button type="button" disabled={busy} onClick={() => void signOutOthers()}>
            {t("settings:sessions.signOutOthers")}
          </button>
        </div>
      )}
      {isAdmin && days && (
        <>
          <h4 className="section-title">{t("settings:sessions.lifetimeTitle")}</h4>
          <p className="detail">{t("settings:sessions.lifetimeHelp")}</p>
          <div className="button-row">
            <label>
              {t("settings:sessions.browserDays")}
              <input
                type="number"
                min={1}
                max={365}
                value={days.browser}
                onChange={(e) => setDays({ ...days, browser: e.target.value })}
              />
            </label>
            <label>
              {t("settings:sessions.appDays")}
              <input
                type="number"
                min={1}
                max={365}
                value={days.app}
                onChange={(e) => setDays({ ...days, app: e.target.value })}
              />
            </label>
            <button type="button" disabled={busy} onClick={() => void saveDays()}>
              {t("settings:sessions.save")}
            </button>
          </div>
        </>
      )}
      {notice && <p className={notice.ok ? "status ok" : "status fail"}>{notice.text}</p>}
    </div>
  );
}
