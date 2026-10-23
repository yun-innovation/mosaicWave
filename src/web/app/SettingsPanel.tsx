"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { apiFetch, AppUser, HostSettings, Me, readDetail, Source } from "./api";
import { FolderPicker } from "./FolderPicker";
import i18n from "./i18n";
import { LocaleSelect } from "./LocaleSelect";
import type { TakeoutImport } from "./useTakeoutImport";

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
    isDebugWeb() ? i18n.t("settings:save.noRestartDebug") : i18n.t("settings:save.noRestart"),
  );
}

type Props = {
  me: Me;
  platform: string;
  importer: TakeoutImport;
  onMe: (me: Me) => void;
};

export function SettingsPanel({ me, platform, importer, onMe }: Props) {
  const { t } = useTranslation(["settings", "common", "gallery"]);
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
          setError(hostRes.status === 401 ? i18n.t("settings:signInAgain", { detail }) : detail);
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
      setError(err instanceof Error ? err.message : i18n.t("settings:loadFailed"));
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
      setError(nas ? t("settings:save.enterHostShare") : t("settings:save.chooseFolder"));
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
        home ? t("settings:save.confirmHome") : t("settings:save.confirmMove"),
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
        setNotice(t("settings:save.restarting"));
        await waitForApiAfterRestart();
        await load();
      }
      setNotice(saved.notice || null);
    } catch (err: unknown) {
      setNotice(null);
      const msg = err instanceof Error ? err.message : t("settings:save.failed");
      setError(
        msg === "Failed to fetch"
          ? isDebugWeb()
            ? t("settings:save.unreachableDebug")
            : t("settings:save.unreachable")
          : msg,
      );
    } finally {
      setBusy(false);
    }
  }

  const library = sources?.find((row) => row.owned) ?? sources?.[0];
  const storageLocked = host?.env_override === true;
  return (
    <section className="card settings">
      <div className="gallery-head">
        <h2>{t("settings:title")}</h2>
      </div>
      <div className="import">
        <label htmlFor="ui_locale">{t("settings:language.label")}</label>
        <p className="detail">{t("settings:language.help")}</p>
        <LocaleSelect id="ui_locale" me={me} onMe={onMe} />
      </div>
      <div className="import">
        <label>{t("settings:thumbnails.label")}</label>
        <p className="detail">{t("settings:thumbnails.help")}</p>
        <div className="button-row">
          <button type="button" disabled={importer.busy} onClick={() => void importer.generateThumbs()}>
            {importer.busy ? t("common:working") : t("gallery:generateThumbs")}
          </button>
        </div>
        {importer.message && <p className="detail import-progress">{importer.message}</p>}
      </div>
      {error && <p className="status fail">{error}</p>}
      {notice && !error && <p className="detail">{notice}</p>}
      {sources === null && !error && <p className="detail">{t("common:loading")}</p>}
      {library && (
        <p className="detail">
          {t("settings:library.summary", { count: library.asset_count })}
          {library.error ? ` · ${library.error}` : ""}
        </p>
      )}
      {me.role === "admin" && (
        <>
          {host && (
            <div className="import">
              <label htmlFor="app_dir">{t("settings:appDir.label")}</label>
              <p className="detail">{t("settings:appDir.help")}</p>
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
              <p className="detail">{t("settings:appDir.schema", { version: host.schema_version })}</p>
              <label htmlFor="library_dir">{t("settings:current.label")}</label>
              <p className="detail">{t("settings:current.help")}</p>
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
              <label>{t("settings:move.label")}</label>
              <p className="detail">{t("settings:move.help")}</p>
              <div className="dest-tabs" role="tablist" aria-label={t("settings:move.label")}>
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
                  {t("settings:move.folder")}
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
                  {t("settings:move.smb")}
                </button>
              </div>
              {destKind === "nas" && host.data_dir_smb && (
                <p className="detail">
                  {host.data_dir_mount
                    ? t("settings:move.remountsAt", { url: host.data_dir_smb, mount: host.data_dir_mount })
                    : t("settings:move.remounts", { url: host.data_dir_smb })}
                </p>
              )}
              {storageLocked && (
                <p className="detail">
                  {platform === "qnap" ? t("settings:move.lockedQnap") : t("settings:move.lockedEnv")}
                </p>
              )}
              <div className="path-row">
                {destKind === "nas" ? (
                  <div className="smb-grid">
                    <label htmlFor="smb_host">
                      {t("settings:smb.host")}
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
                      {t("settings:smb.share")}
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
                      {t("settings:smb.folder")}
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
                      {t("common:browse")}
                    </button>
                  </>
                )}
              </div>
              {destKind === "nas" && (
                <>
                  <label>{t("settings:dest.label")}</label>
                  <p className="detail">{t("settings:dest.help")}</p>
                  <div className="path-row dest-split-row">
                    <div className="dest-split">
                      <span className="dest-prefix" aria-hidden="true">
                        /Volumes
                      </span>
                      <label htmlFor="smb_mount">
                        {t("settings:dest.mountPoint")}
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
                        {t("settings:dest.folder")}
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
                      {t("common:browse")}
                    </button>
                  </div>
                  <label htmlFor="smb_user">{t("settings:nas.userLabel")}</label>
                  <p className="detail">{t("settings:nas.userHelp")}</p>
                  <div className="path-row">
                    <input
                      id="smb_user"
                      name="smb_user"
                      value={smbUserDraft}
                      onChange={(e) => setSmbUserDraft(e.target.value)}
                      disabled={storageLocked || busy}
                      autoComplete="off"
                      placeholder={t("settings:nas.userPlaceholder")}
                    />
                  </div>
                  <label htmlFor="smb_password">{t("settings:nas.passwordLabel")}</label>
                  <div className="path-row">
                    <input
                      id="smb_password"
                      name="smb_password"
                      type="password"
                      value={smbPasswordDraft}
                      onChange={(e) => setSmbPasswordDraft(e.target.value)}
                      disabled={storageLocked || busy}
                      autoComplete="new-password"
                      placeholder={t("settings:nas.passwordPlaceholder")}
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
                  {t("settings:move.save")}
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
                  {t("settings:move.useAppFolder")}
                </button>
              </div>
            </div>
          )}
          {platform === "qnap" ? (
            <p className="detail">{t("settings:users.qnap")}</p>
          ) : (
            <>
              <form aria-hidden="true" autoComplete="on" className="autofill-decoy">
                <input type="text" name="username" autoComplete="username" tabIndex={-1} />
                <input type="password" name="password" autoComplete="current-password" tabIndex={-1} />
              </form>
              <form className="import" autoComplete="off" onSubmit={createUser}>
                <label htmlFor="add_username">{t("settings:users.addMember")}</label>
                {users && users.length > 0 && (
                  <p className="detail">
                    {users
                      .filter((u) => u.id !== me.id)
                      .map((u) => u.username)
                      .join(", ") || t("settings:users.noOthers")}
                  </p>
                )}
                <input
                  id="add_username"
                  name="add_username"
                  placeholder={t("settings:users.username")}
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
                  placeholder={t("settings:users.displayName")}
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
                  placeholder={t("settings:users.password")}
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
                    {t("settings:users.create")}
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
            pickerKind === "mount" ? t("settings:move.pickerMount") : t("settings:move.pickerLibrary")
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
