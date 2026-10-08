"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { apiFetch, AppUser, Grant, HostSettings, Me, readDetail, Source } from "./api";
import { FolderPicker } from "./FolderPicker";

type SmbParts = { host: string; share: string; folder: string; user: string };

/** next dev always runs on :3000 (scripts/start.sh). Packaged ports vary (installer-chosen). */
function isDebugWeb(): boolean {
  return typeof window !== "undefined" && window.location.port === "3000";
}

function looksLikeRemoteShare(path: string): boolean {
  const lower = path.trim().toLowerCase();
  return (
    lower.startsWith("smb://") ||
    lower.startsWith("cifs://") ||
    lower.startsWith("\\\\") ||
    /^\/\/[^/]/.test(lower)
  );
}

function stripSmbScheme(path: string): string {
  return path.trim().replace(/^(?:smb|cifs):\/\//i, "").replace(/^\/\//, "");
}

function parseSmbParts(raw: string): SmbParts {
  let body = stripSmbScheme(raw).replace(/\\/g, "/");
  let user = "";
  const slash = body.indexOf("/");
  const at = body.indexOf("@");
  if (at > 0 && (slash < 0 || at < slash)) {
    user = decodeURIComponent(body.slice(0, at).split(":")[0] || "");
    body = body.slice(at + 1);
  }
  const parts = body.split("/").filter(Boolean);
  return {
    host: parts[0] || "",
    share: parts[1] || "",
    folder: parts.slice(2).join("/"),
    user,
  };
}

function joinSmbUrl(host: string, share: string, folder: string): string {
  const h = host.trim();
  const s = share.trim();
  const f = folder.trim().replace(/^\/+|\/+$/g, "");
  if (!h || !s) {
    return "";
  }
  return f ? `smb://${h}/${s}/${f}` : `smb://${h}/${s}`;
}

function volumeNameFromPath(raw: string): string {
  let text = raw.trim().replace(/\\/g, "/");
  if (text.toLowerCase().startsWith("/volumes/")) {
    text = text.slice("/Volumes/".length);
  } else if (text.toLowerCase() === "/volumes") {
    return "";
  }
  return text.split("/").filter(Boolean)[0] || "";
}

function folderFromVolumesPath(raw: string): string {
  let text = raw.trim().replace(/\\/g, "/");
  if (text.toLowerCase().startsWith("/volumes/")) {
    text = text.slice("/Volumes/".length);
  }
  const parts = text.split("/").filter(Boolean);
  return parts.slice(1).join("/");
}

function joinMountPoint(name: string): string {
  const volume = volumeNameFromPath(name);
  return volume ? `/Volumes/${volume}` : "";
}

function hostLooksNas(next: HostSettings): boolean {
  return Boolean(next.data_dir_smb) || looksLikeRemoteShare(next.data_dir);
}

async function waitForApiAfterRestart() {
  let sawDown = false;
  for (let i = 0; i < 90; i++) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    try {
      const res = await apiFetch("/api/v1/health");
      if (res.ok) {
        if (sawDown || i >= 4) {
          return;
        }
      } else {
        sawDown = true;
      }
    } catch {
      sawDown = true;
    }
  }
  throw new Error(
    isDebugWeb()
      ? "API did not come back after Save folder. Run sudo sh scripts/start.sh."
      : "API did not come back after Save folder. It should restart on its own — wait and retry.",
  );
}

type Props = {
  me: Me;
  platform: string;
  onClose: () => void;
};

export function SettingsPanel({ me, platform, onClose }: Props) {
  const [sources, setSources] = useState<Source[] | null>(null);
  const [users, setUsers] = useState<AppUser[] | null>(null);
  const [host, setHost] = useState<HostSettings | null>(null);
  const [dataDirDraft, setDataDirDraft] = useState("");
  const [smbHostDraft, setSmbHostDraft] = useState("");
  const [smbShareDraft, setSmbShareDraft] = useState("");
  const [smbFolderDraft, setSmbFolderDraft] = useState("");
  const [mountPointDraft, setMountPointDraft] = useState("");
  const [smbUserDraft, setSmbUserDraft] = useState("");
  const [smbPasswordDraft, setSmbPasswordDraft] = useState("");
  const [destKind, setDestKind] = useState<"folder" | "nas">("folder");
  const [pickerKind, setPickerKind] = useState<"library" | "mount" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [grants, setGrants] = useState<Grant[] | null>(null);
  const [shareUserId, setShareUserId] = useState("");
  const [sharePerm, setSharePerm] = useState<"read" | "write">("read");
  const [newUser, setNewUser] = useState({ username: "", password: "", display_name: "" });
  const [memberFieldsLocked, setMemberFieldsLocked] = useState(true);

  const applyHost = useCallback((next: HostSettings) => {
    setHost(next);
    const nas = hostLooksNas(next);
    setDestKind(nas ? "nas" : "folder");
    setDataDirDraft(nas ? next.default_data_dir : next.data_dir);
    const parts = parseSmbParts(next.data_dir_smb || "");
    setSmbHostDraft(parts.host);
    setSmbShareDraft(parts.share);
    setSmbFolderDraft(parts.folder);
    setSmbUserDraft(next.smb_user || parts.user || "");
    setMountPointDraft("");
  }, []);

  const load = useCallback(async () => {
    setError(null);
    try {
      if (me.role === "admin") {
        const hostRes = await apiFetch("/api/v1/settings");
        if (hostRes.ok) {
          applyHost((await hostRes.json()) as HostSettings);
        } else {
          const detail = await readDetail(hostRes);
          setError(
            hostRes.status === 401
              ? `${detail}. Reload and sign in. If a NAS move just failed, copy library.db back to the default folder before Create admin.`
              : detail,
          );
        }
      }
      const srcRes = await apiFetch("/api/v1/sources");
      if (!srcRes.ok) {
        const detail = await readDetail(srcRes);
        setSources([]);
        setError((prev) => prev || detail);
        return;
      }
      const rows = (await srcRes.json()) as Source[];
      setSources(rows);
      const mine = rows.find((row) => row.owned) ?? rows[0];
      if (mine) {
        const grantRes = await apiFetch(`/api/v1/sources/${mine.id}/grants`);
        if (!grantRes.ok) {
          const detail = await readDetail(grantRes);
          setError((prev) => prev || detail);
        } else {
          setGrants((await grantRes.json()) as Grant[]);
        }
      }
      const userRes = await apiFetch("/api/v1/users");
      if (!userRes.ok) {
        const detail = await readDetail(userRes);
        setError((prev) => prev || detail);
        setUsers([]);
        return;
      }
      setUsers((await userRes.json()) as AppUser[]);
    } catch (err: unknown) {
      setSources((prev) => prev ?? []);
      setError(err instanceof Error ? err.message : "failed to load settings");
    }
  }, [applyHost, me.role]);

  useEffect(() => {
    void load();
  }, [load]);

  async function createUser(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await apiFetch("/api/v1/users", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...newUser, role: "member" }),
      });
      if (!res.ok) {
        setError(await readDetail(res));
        return;
      }
      setNewUser({ username: "", password: "", display_name: "" });
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function saveDataDir() {
    if (!host) return;
    const nas = destKind === "nas";
    const next = nas
      ? joinSmbUrl(smbHostDraft, smbShareDraft, smbFolderDraft)
      : dataDirDraft.trim();
    const mount = joinMountPoint(mountPointDraft);
    if (!next) {
      setError(nas ? "Enter host and share" : "Choose a folder");
      return;
    }
    const sameSource =
      next === host.data_dir ||
      next === host.data_dir_smb ||
      next === host.default_data_dir ||
      (nas && next === host.data_dir_smb);
    const sameMount = !nas || !mount || mount === (host.data_dir_mount || "");
    if (!sameSource || !sameMount) {
      const home = next === host.app_dir || next === host.default_data_dir;
      const ok = window.confirm(
        home
          ? "Move the library back to the app folder? Leftover library files there are replaced. Config and login stay. Browse and typing do not move anything until you confirm here."
          : "Move the library database, photos, and thumbnails to this destination? Browse and typing do not move anything until you confirm here.",
      );
      if (!ok) return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const res = await apiFetch("/api/v1/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          data_dir: next,
          smb_user: nas ? smbUserDraft.trim() || undefined : undefined,
          smb_password: nas ? smbPasswordDraft || undefined : undefined,
          mount_point: nas ? mount || undefined : undefined,
        }),
      });
      if (!res.ok) {
        setError(await readDetail(res));
        const latest = await apiFetch("/api/v1/settings");
        if (latest.ok) {
          applyHost((await latest.json()) as HostSettings);
        }
        return;
      }
      const saved = (await res.json()) as HostSettings;
      applyHost(saved);
      setSmbPasswordDraft("");
      if (saved.restarting) {
        setNotice("Restarting the API to open the new library folder…");
        await waitForApiAfterRestart();
        await load();
      }
      setNotice(saved.notice || null);
    } catch (err: unknown) {
      setNotice(null);
      const msg = err instanceof Error ? err.message : "failed to save folder";
      setError(
        msg === "Failed to fetch"
          ? isDebugWeb()
            ? "Could not reach the API (Save folder may still be mounting). Wait and retry, or restart with sudo sh scripts/start.sh."
            : "Could not reach the API (Save folder may still be mounting). Wait and retry — it should restart on its own."
          : msg,
      );
    } finally {
      setBusy(false);
    }
  }

  async function putGrants(next: { user_id: string; perm: string }[]) {
    if (!library) return;
    setBusy(true);
    setError(null);
    try {
      const res = await apiFetch(`/api/v1/sources/${library.id}/grants`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ grants: next }),
      });
      if (!res.ok) {
        setError(await readDetail(res));
        return;
      }
      setGrants((await res.json()) as Grant[]);
      setShareUserId("");
    } finally {
      setBusy(false);
    }
  }

  async function addShare(event: FormEvent) {
    event.preventDefault();
    if (!shareUserId) {
      setError("Choose a person to share with");
      return;
    }
    const next = (grants ?? []).filter((row) => row.user_id !== shareUserId);
    next.push({ user_id: shareUserId, perm: sharePerm, username: "", display_name: "" });
    await putGrants(next.map((row) => ({ user_id: row.user_id, perm: row.perm })));
  }

  const library = sources?.find((row) => row.owned) ?? sources?.[0];
  const storageLocked = host?.env_override === true;
  const shareCandidates =
    users?.filter(
      (row) => row.id !== me.id && !(grants ?? []).some((g) => g.user_id === row.id),
    ) ?? [];

  return (
    <section className="card settings">
      <div className="gallery-head">
        <h2>Settings</h2>
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>
      {error && <p className="status fail">{error}</p>}
      {notice && !error && <p className="detail">{notice}</p>}
      {sources === null && !error && <p className="detail">Loading…</p>}
      {library && (
        <p className="detail">
          Your library · {library.asset_count} assets
          {library.error ? ` · ${library.error}` : ""}
        </p>
      )}
      {library && (
        <form className="import" onSubmit={addShare}>
          <label htmlFor="share_user">Share this library</label>
          <p className="detail">
            Read: timeline, thumbs, originals, albums, sync. Write: also upload and edit albums. They
            keep their own library. Admin still cannot see yours unless you share.
          </p>
          {(grants ?? []).map((row) => (
            <div className="grant-row" key={row.user_id}>
              <span>
                {row.display_name || row.username} · {row.perm}
              </span>
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  void putGrants(
                    (grants ?? [])
                      .filter((g) => g.user_id !== row.user_id)
                      .map((g) => ({ user_id: g.user_id, perm: g.perm })),
                  )
                }
              >
                Remove
              </button>
            </div>
          ))}
          {users && users.filter((u) => u.id !== me.id).length === 0 && (
            <p className="detail">
              {platform === "qnap"
                ? "The other person must sign in with their QNAP account once before you can share."
                : "Create a member below, then share with them here."}
            </p>
          )}
          {shareCandidates.length > 0 && (
            <div className="grant-row">
              <select
                id="share_user"
                value={shareUserId}
                onChange={(e) => setShareUserId(e.target.value)}
                disabled={busy}
              >
                <option value="">Choose a person…</option>
                {shareCandidates.map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.display_name || row.username}
                  </option>
                ))}
              </select>
              <select
                aria-label="Share permission"
                value={sharePerm}
                onChange={(e) => setSharePerm(e.target.value as "read" | "write")}
                disabled={busy}
              >
                <option value="read">Read</option>
                <option value="write">Write</option>
              </select>
              <button type="submit" disabled={busy || !shareUserId}>
                Share
              </button>
            </div>
          )}
        </form>
      )}
      {me.role === "admin" && (
        <>
          {host && (
            <div className="import">
              <label htmlFor="app_dir">App folder</label>
              <p className="detail">
                Config, session key, SQLite, and packaged Python stay here. This is not the photo
                library. On a packaged Mac install it is /Library/Application Support/mosaicWave.
                Local debug uses /Library/Application Support/mosaicWave-dev.
              </p>
              <div className="path-row">
                <input
                  id="app_dir"
                  name="app_dir"
                  value={host.app_dir}
                  readOnly
                  disabled
                  autoComplete="off"
                />
              </div>
              <label htmlFor="library_dir">Current library</label>
              <p className="detail">
                Photos and thumbnails in use now. SQLite stays in the App folder above.
              </p>
              <div className="path-row">
                <input
                  id="library_dir"
                  name="library_dir"
                  value={host.data_dir}
                  readOnly
                  disabled
                  autoComplete="off"
                />
              </div>
              <label>Move library</label>
              <p className="detail">
                One destination: a local folder, or an SMB share. Photos and thumbnails move only
                when you click Save folder. SQLite stays in the App folder. Returning to the app
                folder replaces leftover library files there; config and login stay. If the move
                fails, mosaicWave keeps the original folder.
              </p>
              <div className="dest-tabs" role="tablist" aria-label="Move library">
                <button
                  type="button"
                  role="tab"
                  aria-selected={destKind === "folder"}
                  disabled={storageLocked || busy}
                  onClick={() => {
                    setDestKind("folder");
                    if (destKind === "nas") {
                      setDataDirDraft(host.data_dir_smb ? host.default_data_dir : host.data_dir);
                    }
                  }}
                >
                  Folder
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={destKind === "nas"}
                  disabled={storageLocked || busy}
                  onClick={() => {
                    setDestKind("nas");
                    const parts = parseSmbParts(host.data_dir_smb || "");
                    setSmbHostDraft(parts.host);
                    setSmbShareDraft(parts.share);
                    setSmbFolderDraft(parts.folder);
                    if (parts.user && !smbUserDraft) {
                      setSmbUserDraft(parts.user);
                    }
                    setMountPointDraft("");
                  }}
                >
                  SMB
                </button>
              </div>
              {destKind === "nas" && host.data_dir_smb && (
                <p className="detail">
                  Remounts at startup as {host.data_dir_smb}
                  {host.data_dir_mount ? ` at ${host.data_dir_mount}` : ""}
                </p>
              )}
              {storageLocked && (
                <p className="detail">
                  {platform === "qnap"
                    ? "This QPKG keeps the library in a hidden .mosaicWave folder on the volume (not inside the app folder). Remove and reinstall does not delete it."
                    : "MOSAICWAVE_DATA_DIR is set on this process, so the folder is read-only here."}
                </p>
              )}
              <div className="path-row">
                {destKind === "nas" ? (
                  <div className="smb-grid">
                    <label htmlFor="smb_host">
                      Host
                      <input
                        id="smb_host"
                        name="smb_host"
                        value={smbHostDraft}
                        onChange={(e) => setSmbHostDraft(e.target.value.trim())}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") e.preventDefault();
                        }}
                        disabled={storageLocked || busy}
                        autoComplete="off"
                        placeholder="fileserver"
                      />
                    </label>
                    <label htmlFor="smb_share">
                      Share
                      <input
                        id="smb_share"
                        name="smb_share"
                        value={smbShareDraft}
                        onChange={(e) => setSmbShareDraft(e.target.value.trim())}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") e.preventDefault();
                        }}
                        disabled={storageLocked || busy}
                        autoComplete="off"
                        placeholder="Public"
                      />
                    </label>
                    <label htmlFor="smb_folder">
                      Folder
                      <input
                        id="smb_folder"
                        name="smb_folder"
                        value={smbFolderDraft}
                        onChange={(e) => setSmbFolderDraft(e.target.value.replace(/^\/+/, ""))}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") e.preventDefault();
                        }}
                        disabled={storageLocked || busy}
                        autoComplete="off"
                        placeholder="mosaicWave"
                      />
                    </label>
                  </div>
                ) : (
                  <>
                    <input
                      id="data_dir"
                      name="data_dir"
                      value={dataDirDraft}
                      onChange={(e) => {
                        const value = e.target.value;
                        if (looksLikeRemoteShare(value)) {
                          const parts = parseSmbParts(value);
                          setDestKind("nas");
                          setSmbHostDraft(parts.host);
                          setSmbShareDraft(parts.share);
                          setSmbFolderDraft(parts.folder);
                          if (parts.user && !smbUserDraft) {
                            setSmbUserDraft(parts.user);
                          }
                          setMountPointDraft("");
                          return;
                        }
                        setDataDirDraft(value);
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") e.preventDefault();
                      }}
                      disabled={storageLocked || busy}
                      autoComplete="off"
                      placeholder={host.default_data_dir}
                    />
                    <button
                      type="button"
                      disabled={storageLocked || busy}
                      onClick={() => setPickerKind("library")}
                    >
                      Browse…
                    </button>
                  </>
                )}
              </div>
              {destKind === "nas" && (
                <>
                  <label>Destination</label>
                  <p className="detail">
                    Always under /Volumes. Mount point is the volume name only. Folder is the
                    same library path as SMB (rest after the share). Leave mount point blank for
                    mosaicWave-…. Last dest is shown above, not typed back in. Do not pick Finder
                    Public.
                  </p>
                  <div className="path-row dest-split-row">
                    <div className="dest-split">
                      <span className="dest-prefix" aria-hidden="true">
                        /Volumes
                      </span>
                      <label htmlFor="smb_mount">
                        Mount point
                        <input
                          id="smb_mount"
                          name="smb_mount"
                          value={mountPointDraft}
                          onChange={(e) => setMountPointDraft(volumeNameFromPath(e.target.value))}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") e.preventDefault();
                          }}
                          disabled={storageLocked || busy}
                          autoComplete="off"
                          placeholder="mosaicWave-fileserver-Public"
                        />
                      </label>
                      <label htmlFor="dest_folder">
                        Folder
                        <input
                          id="dest_folder"
                          name="dest_folder"
                          value={smbFolderDraft}
                          onChange={(e) => setSmbFolderDraft(e.target.value.replace(/^\/+/, ""))}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") e.preventDefault();
                          }}
                          disabled={storageLocked || busy}
                          autoComplete="off"
                          placeholder="mosaicWave"
                        />
                      </label>
                    </div>
                    <button
                      type="button"
                      disabled={storageLocked || busy}
                      onClick={() => setPickerKind("mount")}
                    >
                      Browse…
                    </button>
                  </div>
                  <label htmlFor="smb_user">NAS username</label>
                  <p className="detail">
                    QNAP account for this share, not the mosaicWave login. Do not put user@ in the
                    host box. Password is required on first Save folder. Names starting with a dot
                    are reserved on QNAP shares.
                  </p>
                  <div className="path-row">
                    <input
                      id="smb_user"
                      name="smb_user"
                      value={smbUserDraft}
                      onChange={(e) => setSmbUserDraft(e.target.value)}
                      disabled={storageLocked || busy}
                      autoComplete="off"
                      placeholder="username"
                    />
                  </div>
                  <label htmlFor="smb_password">NAS password</label>
                  <div className="path-row">
                    <input
                      id="smb_password"
                      name="smb_password"
                      type="password"
                      value={smbPasswordDraft}
                      onChange={(e) => setSmbPasswordDraft(e.target.value)}
                      disabled={storageLocked || busy}
                      autoComplete="new-password"
                      placeholder="required on first Save folder"
                    />
                  </div>
                </>
              )}
              <div className="button-row">
                <button
                  type="button"
                  disabled={storageLocked || busy}
                  onClick={() => void saveDataDir()}
                >
                  Save folder
                </button>
                <button
                  type="button"
                  disabled={storageLocked || busy}
                  onClick={() => {
                    setDestKind("folder");
                    setDataDirDraft(host.default_data_dir);
                    setMountPointDraft("");
                  }}
                >
                  Use app folder
                </button>
              </div>
            </div>
          )}
          {platform === "qnap" ? (
            <p className="detail">
              People sign in with their QNAP account. mosaicWave does not add NAS users here.
            </p>
          ) : (
            <>
              <form aria-hidden="true" autoComplete="on" className="autofill-decoy">
                <input type="text" name="username" autoComplete="username" tabIndex={-1} />
                <input type="password" name="password" autoComplete="current-password" tabIndex={-1} />
              </form>
              <form className="import" autoComplete="off" onSubmit={createUser}>
                <label htmlFor="add_username">Add member</label>
                {users && users.length > 0 && (
                  <p className="detail">
                    {users
                      .filter((u) => u.id !== me.id)
                      .map((u) => u.username)
                      .join(", ") || "No other users yet."}
                  </p>
                )}
                <input
                  id="add_username"
                  name="add_username"
                  placeholder="Username"
                  value={newUser.username}
                  onChange={(e) => setNewUser({ ...newUser, username: e.target.value })}
                  autoComplete="off"
                  data-1p-ignore
                  data-lpignore="true"
                  readOnly={memberFieldsLocked}
                  onFocus={() => setMemberFieldsLocked(false)}
                  required
                />
                <input
                  id="add_display_name"
                  name="add_display_name"
                  placeholder="Display name"
                  value={newUser.display_name}
                  onChange={(e) => setNewUser({ ...newUser, display_name: e.target.value })}
                  autoComplete="off"
                  data-1p-ignore
                  data-lpignore="true"
                  readOnly={memberFieldsLocked}
                  onFocus={() => setMemberFieldsLocked(false)}
                />
                <input
                  id="add_password"
                  name="add_password"
                  type="password"
                  placeholder="Password (8+)"
                  value={newUser.password}
                  onChange={(e) => setNewUser({ ...newUser, password: e.target.value })}
                  autoComplete="new-password"
                  data-1p-ignore
                  data-lpignore="true"
                  readOnly={memberFieldsLocked}
                  onFocus={() => setMemberFieldsLocked(false)}
                  required
                  minLength={8}
                />
                <div className="button-row">
                  <button type="submit" disabled={busy}>
                    Create member
                  </button>
                </div>
              </form>
            </>
          )}
        </>
      )}
      {pickerKind && (
        <FolderPicker
          startPath={
            pickerKind === "mount"
              ? joinMountPoint(mountPointDraft) || "/Volumes"
              : dataDirDraft || host?.default_data_dir || ""
          }
          title={
            pickerKind === "mount" ? "Select SMB mount point" : "Select library storage folder"
          }
          onSelect={(path) => {
            if (pickerKind === "mount") {
              setMountPointDraft(volumeNameFromPath(path));
              const under = folderFromVolumesPath(path);
              if (under) {
                setSmbFolderDraft(under);
              }
            } else {
              setDataDirDraft(path);
            }
            setPickerKind(null);
          }}
          onClose={() => setPickerKind(null)}
        />
      )}
    </section>
  );
}
