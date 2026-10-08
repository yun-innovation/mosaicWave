"use client";

import { FormEvent, useState } from "react";

import { BrandLockup } from "./BrandLockup";
import { apiFetch, Me, readDetail } from "./api";

type Props = {
  mode: "setup" | "login";
  platform: string;
  onAuthed: (me: Me) => void;
};

export function AuthScreen({ mode, platform, onAuthed }: Props) {
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
      setError(err instanceof Error ? err.message : "request failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main>
      <BrandLockup lede={mode === "setup" ? "Create the first admin account" : "Sign in"} />
      <form className="card import" onSubmit={onSubmit}>
        <p className="detail">
          {platform === "qnap" ? "QNAP account" : "Local account"}
          {mode === "setup" ? " · first user is admin" : ""}
        </p>
        <label htmlFor="auth-user">Username</label>
        <input
          id="auth-user"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoComplete="username"
          required
        />
        {mode === "setup" && (
          <>
            <label htmlFor="auth-name">Display name</label>
            <input
              id="auth-name"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              autoComplete="nickname"
            />
          </>
        )}
        <label htmlFor="auth-pass">Password</label>
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
            {busy ? "Working…" : mode === "setup" ? "Create admin" : "Sign in"}
          </button>
        </div>
        {error && <p className="status fail">{error}</p>}
      </form>
    </main>
  );
}
