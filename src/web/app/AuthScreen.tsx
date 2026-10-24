"use client";

import { FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { BrandLockup } from "./BrandLockup";
import { apiFetch, Me, readDetail } from "./api";

type Props = {
  mode: "setup" | "login";
  platform: string;
  onAuthed: (me: Me) => void;
  /** Maintenance: back to the alert page. */
  onBack?: () => void;
};

export function AuthScreen({ mode, platform, onAuthed, onBack }: Props) {
  const { t } = useTranslation(["auth", "common", "maintenance"]);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const url = mode === "setup" ? "/api/v1/auth/setup" : "/api/v1/auth/login";
      const body =
        mode === "setup"
          ? { username, password, display_name: displayName }
          : { username, password };
      const res = await apiFetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        setError(await readDetail(res));
        return;
      }
      onAuthed((await res.json()) as Me);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("common:requestFailed"));
    } finally {
      setBusy(false);
    }
  }

  const account = platform === "qnap" ? t("auth:account.qnap") : t("auth:account.local");

  return (
    <main>
      <BrandLockup lede={mode === "setup" ? t("auth:lede.setup") : t("auth:lede.login")} />
      <form className="card import" onSubmit={onSubmit}>
        <p className="detail">
          {mode === "setup" ? t("auth:account.firstAdmin", { account }) : account}
        </p>
        <label htmlFor="auth-user">{t("auth:username")}</label>
        <input
          id="auth-user"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoComplete="username"
          required
        />
        {mode === "setup" && (
          <>
            <label htmlFor="auth-name">{t("auth:displayName")}</label>
            <input
              id="auth-name"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              autoComplete="nickname"
            />
          </>
        )}
        <label htmlFor="auth-pass">{t("auth:password")}</label>
        <input
          id="auth-pass"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete={mode === "setup" ? "new-password" : "current-password"}
          required
          minLength={mode === "setup" ? 8 : undefined}
        />
        <div className="button-row">
          <button type="submit" disabled={busy}>
            {busy
              ? t("common:working")
              : mode === "setup"
                ? t("auth:submit.setup")
                : t("auth:submit.login")}
          </button>
          {onBack && (
            <button type="button" onClick={onBack}>
              {t("maintenance:screen.back")}
            </button>
          )}
        </div>
        {error && <p className="status fail">{error}</p>}
      </form>
    </main>
  );
}
