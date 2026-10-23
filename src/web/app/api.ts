import i18n from "./i18n";

export type Me = {
  id: string;
  username: string;
  display_name: string;
  role: string;
  provider: string;
  locale: string | null;
};

export type AuthStatus = {
  platform: string;
  needs_setup: boolean;
  me: Me | null;
};

export type Source = {
  id: string;
  kind: string;
  backend: string;
  last_scan_at: string | null;
  error: string | null;
  asset_count: number;
  perm: string | null;
  owned: boolean;
};

export type AppUser = {
  id: string;
  username: string;
  display_name: string;
  role: string;
  provider: string;
};

export type HostSettings = {
  data_dir: string;
  default_data_dir: string;
  app_dir: string;
  data_dir_smb?: string | null;
  data_dir_mount?: string | null;
  smb_user?: string | null;
  env_override: boolean;
  schema_version: number;
  restarting?: boolean;
  notice?: string | null;
};

export function apiFetch(input: string, init: RequestInit = {}) {
  return fetch(apiUrl(input, init.body), { ...init, credentials: "include" });
}

export function assetFileUrl(assetId: string, download = false): string {
  const path = `/api/v1/assets/${assetId}/file${download ? "?download=1" : ""}`;
  return apiMediaUrl(path);
}

/** next dev rewrite can buffer or time out long Save-folder mounts; hit uvicorn. */
export function apiMediaUrl(path: string): string {
  if (typeof window === "undefined") return path;
  if (window.location.port !== "3000") return path;
  if (!path.startsWith("/")) return path;
  return `${window.location.protocol}//${window.location.hostname}:8000${path}`;
}

function apiUrl(path: string, _body: BodyInit | null | undefined): string {
  return apiMediaUrl(path);
}

type Params = Record<string, unknown>;

const translate = i18n.t.bind(i18n) as unknown as (key: string, options?: Params) => string;

/** `errors:{code}` in the active locale, or null when the code has no translation. */
export function translateCode(code: string, params: Params = {}): string | null {
  const key = `errors:${code}`;
  return i18n.exists(key, params) ? translate(key, params) : null;
}

/** API error body `{detail, code, params}` → text for the UI (falls back to English detail). */
export function errorText(body: unknown, status: number): string {
  if (typeof body === "object" && body) {
    const { detail, code, params } = body as { detail?: unknown; code?: unknown; params?: unknown };
    if (typeof code === "string" && code) {
      const text = translateCode(code, typeof params === "object" && params ? (params as Params) : {});
      if (text) return text;
    }
    if (detail !== undefined) return String(detail);
  }
  return translate("common:httpStatus", { status });
}

export async function readDetail(res: Response): Promise<string> {
  const body: unknown = await res.json().catch(() => null);
  return errorText(body, res.status);
}

export type DumpWarning = {
  code: string;
  severity: string;
  message: string;
  path: string | null;
  params?: Params;
};

export function warningText(w: DumpWarning): string {
  const params = w.params ?? {};
  const text = translateCode(w.code, params);
  if (!text) return w.message;
  if (w.code !== "no_takeout_zips") return text;
  const hint = params.nested
    ? translateCode("no_takeout_zips_hint_nested", params)
    : params.other_names
      ? translateCode("no_takeout_zips_hint_names", params)
      : null;
  return hint ? `${text} ${hint}` : text;
}

export async function saveLocale(locale: string | null): Promise<Me> {
  const res = await apiFetch("/api/v1/me", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ locale }),
  });
  if (!res.ok) throw new Error(await readDetail(res));
  return (await res.json()) as Me;
}
