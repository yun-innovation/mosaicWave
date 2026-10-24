"use client";

import { useCallback, useSyncExternalStore } from "react";

export const VIEWS = ["photos", "albums", "import", "activity", "server", "backup", "maintenance", "settings"] as const;
export type View = (typeof VIEWS)[number];

export type Route = { view: View; albumId: string | null };

const DEFAULT: Route = { view: "photos", albumId: null };

/** `#/albums/{id}` → albums view with that album open; anything unknown → photos. */
export function parseHash(hash: string): Route {
  const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const view = VIEWS.find((v) => v === parts[0]);
  if (!view) return DEFAULT;
  if (view === "albums" && parts[1]) return { view, albumId: decodeURIComponent(parts[1]) };
  return { view, albumId: null };
}

export function routeHash(route: Route): string {
  if (route.view === "albums" && route.albumId) return `#/albums/${encodeURIComponent(route.albumId)}`;
  return `#/${route.view}`;
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
}

function currentHash(): string {
  return window.location.hash;
}

function serverHash(): string {
  return "";
}

/** Current view from `location.hash`; `go` pushes a history entry so Back/Forward work. */
export function useRoute(): [Route, (route: Route) => void] {
  const hash = useSyncExternalStore(subscribe, currentHash, serverHash);
  const go = useCallback((route: Route) => {
    const next = routeHash(route);
    if (window.location.hash !== next) window.location.hash = next;
  }, []);
  return [parseHash(hash), go];
}
