"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { apiFetch, NetworkSettings, readDetail } from "./api";
import { routeHash } from "./hashRoute";
import type { MaintenanceStatus } from "./types";

type Props = {
  maintenance: MaintenanceStatus;
  /** Resolves once the API answers again after a restart (shared with Move library). */
  waitForRestart: () => Promise<void>;
};

function parseAddresses(text: string): string[] {
  return text.split(/[\s,;]+/).filter(Boolean);
}

/**
 * Admin Server page, Network: which addresses the server listens on (this computer only, every
 * interface, or a typed list). Changing it restarts the server, so it needs maintenance mode.
 * Rules and failure handling: doc/qpkg.md § Bind address.
 */
export function NetworkSection({ maintenance, waitForRestart }: Props) {
  const { t } = useTranslation(["settings"]);
  const [net, setNet] = useState<NetworkSettings | null>(null);
  const [mode, setMode] = useState<NetworkSettings["mode"]>("local");
  const [addresses, setAddresses] = useState("");
  const [port, setPort] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await apiFetch("/api/v1/settings/network");
      if (!res.ok) {
        setError(await readDetail(res));
        return;
      }
      const next = (await res.json()) as NetworkSettings;
      setNet(next);
      setMode(next.mode);
      setAddresses(next.addresses.join("\n"));
      setPort(String(next.port));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("settings:loadFailed"));
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  async function save() {
    if (!net) return;
    const list = mode === "list" ? parseAddresses(addresses) : [];
    if (!window.confirm(t("settings:network.confirm"))) return;
    const change: { mode?: NetworkSettings["mode"]; addresses?: string[]; port?: number } = {};
    if (addressDirty) {
      change.mode = mode;
      change.addresses = list;
    }
    if (portDirty) {
      change.port = Number(port);
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const res = await apiFetch("/api/v1/settings/network", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(change),
      });
      if (!res.ok) {
        setError(await readDetail(res));
        return;
      }
      const saved = (await res.json()) as NetworkSettings;
      if (!saved.restarting) {
        setNotice(t("settings:network.savedNoRestart"));
        await load();
        return;
      }
      setNotice(t("settings:network.restarting"));
      try {
        await waitForRestart();
      } catch {
        // The page may no longer be reachable at the old address.
        const where = !addressDirty
          ? net.effective.join(", ")
          : mode === "all"
            ? "0.0.0.0"
            : mode === "local"
              ? "127.0.0.1"
              : list.join(", ");
        setNotice(t("settings:network.moved", { addresses: where, port: portDirty ? port : net.effective_port }));
        return;
      }
      await load();
      setNotice(t("settings:network.saved"));
    } catch (err: unknown) {
      setNotice(null);
      setError(err instanceof Error ? err.message : t("settings:network.failed"));
    } finally {
      setBusy(false);
    }
  }

  const needsMaintenance = maintenance.state !== "on";
  const addressLocked = !net || net.startup_locked || needsMaintenance || busy;
  const portLocked = !net || net.port_startup_locked || needsMaintenance || busy;
  const addressDirty =
    !!net &&
    !net.startup_locked &&
    (mode !== net.mode || (mode === "list" && parseAddresses(addresses).join(",") !== net.addresses.join(",")));
  const portDirty = !!net && !net.port_startup_locked && port.trim() !== String(net.port);
  const saveLocked = needsMaintenance || busy || !(addressDirty || portDirty);

  return (
    <div className="import">
      <h3 className="section-title divided">{t("settings:network.title")}</h3>
      <p className="detail">{t("settings:network.help")}</p>
      {error && <p className="status fail">{error}</p>}
      {notice && !error && <p className="detail">{notice}</p>}
      {net && (
        <>
          <p className="detail">
            {t("settings:network.effective", {
              addresses: net.effective.join(", "),
              port: net.effective_port,
              source: t(`settings:network.source.${net.source}`),
            })}
          </p>
          {net.failure && (
            <p className="status fail">
              {t("settings:network.failure", {
                failed: Object.keys(net.failure.failed).join(", ") || net.failure.wanted.join(", "),
                bound: net.failure.bound.join(", "),
              })}
            </p>
          )}
          {net.startup_locked && <p className="detail">{t("settings:network.startupLocked")}</p>}
          {net.port_startup_locked && <p className="detail">{t("settings:network.portStartupLocked")}</p>}
          {!(net.startup_locked && net.port_startup_locked) && needsMaintenance && !busy && (
            <p className="detail">
              {t("settings:network.needsMaintenance")}{" "}
              <a href={routeHash({ view: "maintenance", albumId: null })}>{t("settings:move.openMaintenance")}</a>
            </p>
          )}
          <div role="radiogroup" aria-label="127.0.0.1">
            <label className="radio-row">
              <input type="radio" name="bind_loopback" checked disabled readOnly /> {t("settings:network.loopbackFixed")}
            </label>
          </div>
          <div role="radiogroup" aria-label={t("settings:network.title")}>
            {(["all", "list"] as const).map((value) => (
              <div key={value}>
                <label className="radio-row">
                  <input
                    type="radio"
                    name="bind_mode"
                    value={value}
                    checked={mode === value}
                    disabled={addressLocked}
                    onChange={() => setMode(value)}
                    onClick={() => {
                      // A checked radio fires only click: clicking it again deselects (127.0.0.1 only).
                      if (mode === value) setMode("local");
                    }}
                  />{" "}
                  {t(`settings:network.mode.${value}`)}
                </label>
                {value === "all" && <p className="detail">{t("settings:network.warnAll")}</p>}
                {value === "list" && mode === "list" && (
                  <>
                    <label htmlFor="bind_addresses">{t("settings:network.listLabel")}</label>
                    <textarea
                      id="bind_addresses"
                      name="bind_addresses"
                      rows={3}
                      value={addresses}
                      onChange={(e) => setAddresses(e.target.value)}
                      disabled={addressLocked}
                      autoComplete="off"
                      spellCheck={false}
                      placeholder="192.0.2.10"
                    />
                  </>
                )}
              </div>
            ))}
          </div>
          <label htmlFor="bind_port">{t("settings:network.portLabel")}</label>
          <input
            id="bind_port"
            name="bind_port"
            type="number"
            min={1024}
            max={65535}
            value={port}
            onChange={(e) => setPort(e.target.value)}
            disabled={portLocked}
            autoComplete="off"
          />
          <div className="button-row">
            <button type="button" disabled={saveLocked} onClick={() => void save()}>
              {t("settings:network.save")}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
