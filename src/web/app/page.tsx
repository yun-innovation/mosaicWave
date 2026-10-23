"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { ActivityView } from "./ActivityView";
import { BackupView } from "./BackupView";
import { AlbumsView } from "./AlbumsView";
import { apiFetch, apiMediaUrl, AuthStatus, Me } from "./api";
import { AppShell } from "./AppShell";
import { AuthScreen } from "./AuthScreen";
import { BrandLockup } from "./BrandLockup";
import i18n, { applyLocale, isLocale, rememberLocale, resolveLocale } from "./i18n";
import { ImportView } from "./ImportView";
import { useRoute } from "./hashRoute";
import { SettingsPanel } from "./SettingsPanel";
import { TimelineView } from "./TimelineView";
import { ignoreAbort, isAbortError, LibraryAlbum } from "./types";
import { UploadDialog } from "./UploadDialog";
import { useTakeoutImport } from "./useTakeoutImport";

export default function Home() {
  const { t } = useTranslation(["common"]);
  const [route, go] = useRoute();
  const [healthError, setHealthError] = useState<string | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [needsSetup, setNeedsSetup] = useState(false);
  const [platform, setPlatform] = useState("standalone");
  const [albums, setAlbums] = useState<LibraryAlbum[] | null>(null);
  const [libraryRev, setLibraryRev] = useState(0);
  const [albumsRev, setAlbumsRev] = useState(0);
  const [uploadOpen, setUploadOpen] = useState(false);

  const savedLocale = me?.locale ?? null;
  useEffect(() => {
    if (isLocale(savedLocale)) rememberLocale(savedLocale);
    void applyLocale(resolveLocale(savedLocale));
  }, [savedLocale]);

  const onLibraryChanged = useCallback(() => {
    setLibraryRev((n) => n + 1);
    setAlbumsRev((n) => n + 1);
  }, []);
  const onAlbumsChanged = useCallback(() => setAlbumsRev((n) => n + 1), []);
  const importer = useTakeoutImport(me, onLibraryChanged);

  useEffect(() => {
    const ctrl = new AbortController();
    fetch(apiMediaUrl("/api/v1/health"), { signal: ctrl.signal, credentials: "include" })
      .then(async (res) => {
        if (!res.ok) {
          setHealthError(i18n.t("common:httpStatus", { status: res.status }));
          return;
        }
        const body: unknown = await res.json();
        const ok =
          typeof body === "object" && body !== null && "status" in body && (body as { status: unknown }).status === "ok";
        setHealthError(ok ? null : i18n.t("common:health.unexpected"));
      })
      .catch((err: unknown) => {
        if (isAbortError(err)) return;
        setHealthError(err instanceof Error ? err.message : i18n.t("common:networkError"));
      });
    return () => ctrl.abort();
  }, []);

  useEffect(() => {
    const ctrl = new AbortController();
    apiFetch("/api/v1/auth/status", { signal: ctrl.signal })
      .then(async (res) => {
        if (!res.ok) {
          setAuthReady(true);
          return;
        }
        const body = (await res.json()) as AuthStatus;
        setPlatform(body.platform);
        setNeedsSetup(body.needs_setup);
        if (body.platform === "qnap" && !body.me) {
          const login = await apiFetch("/api/v1/auth/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: "{}",
            signal: ctrl.signal,
          });
          if (login.ok) {
            setMe((await login.json()) as Me);
            setAuthReady(true);
            return;
          }
        }
        setMe(body.me);
        setAuthReady(true);
      })
      .catch((err: unknown) => {
        if (isAbortError(err)) return;
        setAuthReady(true);
      });
    return () => ctrl.abort();
  }, []);

  const signedIn = me !== null;
  useEffect(() => {
    if (!signedIn) return;
    const ctrl = new AbortController();
    async function load() {
      try {
        const res = await apiFetch("/api/v1/albums", { signal: ctrl.signal });
        if (!res.ok) return;
        setAlbums((await res.json()) as LibraryAlbum[]);
      } catch (err: unknown) {
        if (isAbortError(err)) return;
      }
    }
    ignoreAbort(load());
    return () => ctrl.abort();
  }, [signedIn, albumsRev]);

  async function onLogout() {
    await apiFetch("/api/v1/auth/logout", { method: "POST" });
    setMe(null);
    setNeedsSetup(false);
    setAlbums(null);
    setUploadOpen(false);
  }

  function onAuthed(user: Me) {
    setMe(user);
    setNeedsSetup(false);
  }

  if (!authReady) {
    return (
      <main>
        <BrandLockup lede={t("common:lede")} />
        <div className="card">
          <p className="status loading">{t("common:session.checking")}</p>
        </div>
      </main>
    );
  }

  if (!me) {
    return <AuthScreen mode={needsSetup ? "setup" : "login"} platform={platform} onAuthed={onAuthed} />;
  }

  let view;
  switch (route.view) {
    case "albums":
      view = (
        <AlbumsView
          albumId={route.albumId}
          albums={albums}
          libraryRev={libraryRev}
          go={go}
          onAlbumsChanged={onAlbumsChanged}
          onLibraryChanged={onLibraryChanged}
        />
      );
      break;
    case "import":
      view = <ImportView importer={importer} />;
      break;
    case "activity":
      view = <ActivityView importer={importer} libraryRev={libraryRev} />;
      break;
    case "backup":
      view = me.role === "admin" ? <BackupView importer={importer} /> : <SettingsPanel me={me} platform={platform} importer={importer} onMe={setMe} />;
      break;
    case "settings":
      view = <SettingsPanel me={me} platform={platform} importer={importer} onMe={setMe} />;
      break;
    default:
      view = (
        <section className="card gallery">
          <div className="gallery-head">
            <h2>{t("common:nav.photos")}</h2>
          </div>
          <TimelineView
            albumId={null}
            albums={albums}
            libraryRev={libraryRev}
            onAlbumsChanged={onAlbumsChanged}
            onLibraryChanged={onLibraryChanged}
          />
        </section>
      );
  }

  return (
    <AppShell
      me={me}
      route={route}
      healthError={healthError}
      job={importer.job}
      finished={importer.finished}
      onDismissFinished={importer.dismissFinished}
      onMe={setMe}
      onUpload={() => setUploadOpen(true)}
      onLogout={() => void onLogout()}
    >
      {view}
      {uploadOpen && <UploadDialog onClose={() => setUploadOpen(false)} onUploaded={onLibraryChanged} />}
    </AppShell>
  );
}
