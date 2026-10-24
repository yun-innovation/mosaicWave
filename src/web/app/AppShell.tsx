"use client";

import { ReactNode, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Me } from "./api";
import { LocaleSelect } from "./LocaleSelect";
import { Route, routeHash, View } from "./hashRoute";
import { StatusBar } from "./StatusBar";
import type { FinishedJob } from "./useActiveJob";
import type { ImportJob, MaintenanceStatus, RunningJob } from "./types";
import { runningLabel, runningProgress } from "./useRunningJob";

const ICONS: Record<View | "upload" | "menu", ReactNode> = {
  photos: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <circle cx="9" cy="10" r="2" />
      <path d="m21 16-5-5-9 9" />
    </>
  ),
  albums: (
    <>
      <rect x="6" y="6" width="15" height="14" rx="2" />
      <path d="M3 16V5a2 2 0 0 1 2-2h11" />
    </>
  ),
  import: (
    <>
      <path d="M12 3v12" />
      <path d="m7 10 5 5 5-5" />
      <path d="M4 19h16" />
    </>
  ),
  activity: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  server: (
    <>
      <rect x="3" y="4" width="18" height="7" rx="2" />
      <rect x="3" y="13" width="18" height="7" rx="2" />
      <path d="M7 7.5h.01M7 16.5h.01" />
    </>
  ),
  backup: (
    <>
      <ellipse cx="12" cy="6" rx="8" ry="3" />
      <path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6" />
      <path d="M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" />
    </>
  ),
  maintenance: (
    <>
      <rect x="6" y="4" width="4" height="16" rx="1" />
      <rect x="14" y="4" width="4" height="16" rx="1" />
    </>
  ),
  settings: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v3M12 19v3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M2 12h3M19 12h3M4.9 19.1 7 17M17 7l2.1-2.1" />
    </>
  ),
  upload: (
    <>
      <path d="M12 21V9" />
      <path d="m7 14 5-5 5 5" />
      <path d="M4 5h16" />
    </>
  ),
  menu: <path d="M4 6h16M4 12h16M4 18h16" />,
};

function Icon({ name }: { name: keyof typeof ICONS }) {
  return (
    <svg className="icon" viewBox="0 0 24 24" aria-hidden="true">
      {ICONS[name]}
    </svg>
  );
}

const NAV: View[] = ["photos", "albums", "import", "activity", "server", "backup", "maintenance", "settings"];

/** Views only an admin sees. */
const ADMIN_ONLY: View[] = ["server", "backup", "maintenance"];

/** While maintenance is on an admin gets only these (see `maintenanceRoute` in page.tsx). */
export const MAINTENANCE_VIEWS: View[] = ["backup", "maintenance", "activity"];

type Props = {
  me: Me;
  route: Route;
  /** Set when the API health check failed; shown as a warning in the top bar. */
  healthError: string | null;
  /** Maintenance mode: an admin sees a banner and the short menu. */
  maintenance: MaintenanceStatus;
  /** The job maintenance is waiting for, whoever started it (admin, while starting). */
  waitingFor: RunningJob | null;
  /** The running job and the last finished one, for the status bar. */
  job: ImportJob | null;
  finished: FinishedJob | null;
  onDismissFinished: () => void;
  onJobsChanged: () => void;
  onMe: (me: Me) => void;
  onUpload: () => void;
  onLogout: () => void;
  children: ReactNode;
};

export function AppShell({
  me,
  route,
  healthError,
  maintenance,
  waitingFor,
  job,
  finished,
  onDismissFinished,
  onJobsChanged,
  onMe,
  onUpload,
  onLogout,
  children,
}: Props) {
  const { t } = useTranslation(["common", "gallery", "settings", "maintenance"]);
  const locked = maintenance.state !== "off";
  const [navOpen, setNavOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setNavOpen(false);
    setMenuOpen(false);
  }, [route.view, route.albumId]);

  useEffect(() => {
    if (!navOpen && !menuOpen) return;
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setNavOpen(false);
      setMenuOpen(false);
    }
    function onPointer(event: PointerEvent) {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) setMenuOpen(false);
    }
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onPointer);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onPointer);
    };
  }, [navOpen, menuOpen]);

  const name = me.display_name || me.username;
  const role = me.role === "admin" ? t("common:role.admin") : t("common:role.member");

  return (
    <div className={navOpen ? "app-shell nav-open" : "app-shell"}>
      <header className="top-bar">
        <button
          type="button"
          className="icon-button nav-toggle"
          aria-label={t("common:nav.menu")}
          aria-expanded={navOpen}
          aria-controls="app-sidebar"
          onClick={() => setNavOpen((v) => !v)}
        >
          <Icon name="menu" />
        </button>
        <a className="top-brand" href={routeHash({ view: "photos", albumId: null })}>
          <img src="/mosaicWave-icon.png" width={28} height={28} alt="" />
          <span>mosaicWave</span>
        </a>
        {healthError && (
          <span className="health-warn" role="status" title={healthError}>
            {t("common:health.fail")}
          </span>
        )}
        <div className="top-actions">
          {!locked && (
            <button type="button" className="top-button" onClick={onUpload}>
              <Icon name="upload" />
              <span className="top-button-text">{t("gallery:upload.submit")}</span>
            </button>
          )}
          <div className="account" ref={menuRef}>
            <button
              type="button"
              className="avatar"
              aria-haspopup="true"
              aria-expanded={menuOpen}
              aria-label={t("common:account.menu", { name })}
              onClick={() => setMenuOpen((v) => !v)}
            >
              {name.slice(0, 1).toUpperCase()}
            </button>
            {menuOpen && (
              <div className="account-menu">
                <p className="account-name">{name}</p>
                <p className="detail">{role}</p>
                <label htmlFor="menu_locale">{t("settings:language.label")}</label>
                <LocaleSelect id="menu_locale" me={me} onMe={onMe} />
                {!locked && <a href={routeHash({ view: "settings", albumId: null })}>{t("common:session.settings")}</a>}
                <button type="button" onClick={onLogout}>
                  {t("common:session.signOut")}
                </button>
              </div>
            )}
          </div>
        </div>
      </header>
      <div className="nav-backdrop" onClick={() => setNavOpen(false)} role="presentation" />
      <nav id="app-sidebar" className="sidebar" aria-label={t("common:nav.label")}>
        {NAV.filter((view) =>
          locked ? MAINTENANCE_VIEWS.includes(view) : me.role === "admin" || !ADMIN_ONLY.includes(view),
        ).map((view) => (
          <a
            key={view}
            href={routeHash({ view, albumId: null })}
            aria-current={route.view === view ? "page" : undefined}
            onClick={() => setNavOpen(false)}
          >
            <Icon name={view} />
            <span>{t(`common:nav.${view}`)}</span>
          </a>
        ))}
      </nav>
      <main className="app-main">
        {locked && (
          <div className="maintenance-banner" role="status" aria-live="polite">
            <span>
              {maintenance.state === "draining" ? t("maintenance:banner.draining") : t("maintenance:banner.on")}
            </span>
            {maintenance.state === "draining" && waitingFor && (
              <span className="maintenance-banner-reason">
                {[runningLabel(waitingFor), runningProgress(waitingFor)].filter(Boolean).join(" · ")}
              </span>
            )}
            {maintenance.reason && <span className="maintenance-banner-reason">{maintenance.reason}</span>}
            <a href={routeHash({ view: "maintenance", albumId: null })}>{t("maintenance:banner.manage")}</a>
          </div>
        )}
        <StatusBar job={job} finished={finished} onDismiss={onDismissFinished} onRefresh={onJobsChanged} />
        {children}
      </main>
    </div>
  );
}
