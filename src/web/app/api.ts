export type Me = {
  id: string;
  username: string;
  display_name: string;
  role: string;
  provider: string;
  granted_source_ids: string[] | null;
  write_source_ids: string[] | null;
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
  restarting?: boolean;
  notice?: string | null;
};

export type Grant = {
  user_id: string;
  username: string;
  display_name: string;
  perm: string;
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

export async function readDetail(res: Response): Promise<string> {
  const body: unknown = await res.json().catch(() => null);
  if (typeof body === "object" && body && "detail" in body) {
    return String((body as { detail: unknown }).detail);
  }
  return `HTTP ${res.status}`;
}
