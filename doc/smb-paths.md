# UNC and `smb://` paths

Settings, Browse (`GET /api/v1/fs/list`), and Save folder accept a **host path** or a **remote share URL**. Remote URLs are mapped to a filesystem folder. That is **not** a new FileStore backend: originals still go through `LocalFileStore` on the resolved path. Code: `src/server/mosaicwave/storage/smbpath.py`. Browse lists as **this process** (Mac debug and the installer both run as a system service). It does not retry as the GUI user or treat a Finder mount as readable.

Related: [storage.md](storage.md) (data dir), [filestore.md](filestore.md) (I/O after the path is a folder), [qpkg.md](qpkg.md) (macOS LaunchDaemon).

## Accepted text

Settings **SMB** is host + share + folder (`smb://fileserver/Public/mosaicWave`). **Destination** is `/Volumes` (fixed) + mount-point name + the same folder, joined as `/Volumes/{mount}/{folder}`. Empty mount name omits `mount_point` (unique `mosaicWave-…` volume). The last dest is status text, not typed back into the box.

| Typed in Settings / Browse | Parsed as |
| --- | --- |
| `smb://fileserver/Public/mosaicWave` | host `fileserver`, share `Public`, folder `mosaicWave` |
| `cifs://nas/share/a/b` | same shape (`cifs` = `smb`) |
| `smb://nasuser@fileserver/Public` | user in the URL is accepted, then stripped; username is stored as `[fileserver] username=` |
| `\\fileserver\Public\mosaicWave` | UNC (Windows style) |
| `//fileserver/Public/mosaicWave` | POSIX UNC |

`/Volumes/Public`, `D:\photos`, and `/share/Public` are **not** remote URLs. They are ordinary host paths.

NAS **username** and **password** belong in the Settings boxes, not in the `smb://` URL. `user@host` breaks nsmb.conf lookup (`[HOST]`, not `[user@HOST]`). Save folder:

1. Mount `//nasuser:password@fileserver/Public` (user and password percent-encoded: `@` → `%40`, `:` → `%3A`, `/` → `%2F`) to the **mount point** (Settings field, browsable). Blank mount point is `/Volumes/mosaicWave-fileserver-Public` (debug: `/Volumes/mosaicWave-dev-fileserver-Public`)
2. **Only if that mount lists files:** write `[fileserver] username=nasuser` to `/etc/nsmb.conf` and the password to the **System keychain** (`security add-internet-password … -r "smb " -A -U`). Never put the password in `nsmb.conf` or `config.json`
3. Unmount, then remount the same dest with the simplified spec `//fileserver/Public` (matches `[fileserver]` in nsmb.conf)
4. **Only if that remount lists files:** write-probe the library folder (`mosaicWave/mosaicwave-write-test.tmp`, not a leading-dot name, not the share root)

Do not leave the password blank on the first Save folder. After that, reboot remounts `//fileserver/Public` from nsmb.conf + System keychain. If the unique volume is **already mounted**, boot does not call `find-internet-password` or `add-internet-password` (that dialog is “modify the System keychain”). If `mount_smbfs -N` cannot use the keychain ACL, mosaicWave reads the password **once** from the **System** keychain (`security find-internet-password -w`) and uses `//nasuser:password@fileserver/Public`. The LaunchDaemon does not search the login keychain or extra host aliases (that is what opened Keychain Access many times at boot).

If the blank-mount-point default (`/Volumes/mosaicWave[-dev]-{host}-{share}`) is already mounted by something else (another mosaicWave instance, or a stale mount left behind by an earlier abrupt restart) and this process cannot list it, Save folder never force-unmounts it — that mount might be in active use elsewhere. It tries numbered fallbacks instead (`…-Public-2`, `…-Public-3`, …) and returns a one-time `notice` in the `PUT /settings` response (shown in Settings) saying which dest it actually used. An **explicitly typed** mount point does not fall back; it fails with the existing “already mounted… pick another mount point” message so a deliberate choice is never silently redirected.

**Known limitation — packaged macOS LaunchDaemon mount instability.** A packaged macOS install's API runs as a true LaunchDaemon with no login/audit session (`SessionCreate`, no GUI). An SMB mount it makes can succeed and even be listable for a while, then macOS tears it down on its own — surfacing as `mount_smbfs exit 0 but {dest} was not a usable mount` even though the dest was reachable moments earlier. Debug (`sudo sh scripts/start.sh`, run inside an interactive login session) does not hit this. Longer `_wait_dest_ready` polling and the numbered-fallback dest (above) help with slow mounts, but neither can prevent the OS from reclaiming an already-working, session-less mount later. If a given share proves unstable on packaged macOS, the practical fallback today is to keep the library in the app folder. A possible future fix — not implemented — is to have the LaunchDaemon run `mount_smbfs` as an impersonated local user (`sudo -u <user>`, after `chmod 0777` on the still-empty mountpoint so the non-root user can mount onto it) instead of as root directly. This is unverified: `sudo -u` only changes UID, not the audit session, so it may only fix a permission error without fixing the teardown behavior. It would also need a decision on which local user to impersonate (the console/GUI user only works after someone logs in, which defeats running before login) and where to store that username — `config.json` or the System keychain — not decided.

## What happens after parse

`coerce_host_folder` turns the text into a `Path` the API can `listdir` / copy. Browse and Save folder both go through that. Typing or picking a folder does **not** move the library; only **Save folder** writes `config.json` and copies files.

| Host | Resolved path |
| --- | --- |
| **Windows** | UNC `\\server\share\rest`. The service can use UNC without a mapped drive. |
| **Linux** | `//server/share/rest` (kernel cifs/smb must already be mounted, or the path must work as written). |
| **macOS packaged** | The **mount point** (Settings, browsable) plus the share folder. Blank mount point is `/Volumes/mosaicWave-{host}-{share}/…`. Never Finder `/Volumes/Public` or `/Volumes/Public-1`. |
| **macOS debug** (`sudo sh scripts/start.sh`) | Same unique dest, API runs as **root** so `mount_smbfs` can attach `/Volumes/mosaicWave-dev-{host}-{share}/…`. |

Windows and Linux do not invent a second mount point. macOS does, because the packaged app is a **LaunchDaemon**.

## macOS unique mount (working sequence)

Verified as root:

```
# /etc/nsmb.conf
[fileserver]
username=nasuser

sudo security add-internet-password -a "nasuser" -s "fileserver" -w "…" \
  -D "network password" -r "smb " -T /sbin/mount_smbfs -T /usr/bin/security -A -U \
  /Library/Keychains/System.keychain

sudo mount_smbfs -N -s //nasuser:…@fileserver/Public /Volumes/mosaicWave-fileserver-Public
# after that succeeds: nsmb.conf + System keychain, then:
sudo umount /Volumes/mosaicWave-fileserver-Public
sudo mount_smbfs -N -s //fileserver/Public /Volumes/mosaicWave-fileserver-Public
```

`-s` starts a **new** SMB session at a unique dest so it is independent of any GUI/Finder mount. Packaged dest is `/Volumes/mosaicWave-{host}-{share}`. Debug dest is `/Volumes/mosaicWave-dev-{host}-{share}` when Mount point is blank. The Mount point field is **user input** — mosaicWave does not rewrite it to avoid sharing with another mosaicWave instance or app. If that dest is already a live mount, Save folder reuses it when this process can list it, and does **not** unmount it. If **this** Save folder’s `mount_smbfs` exits 0 but the dest is not listable, it unmounts that new session and retries once. Leftover local dirs (not a mount) get Finder junk (`.DS_Store`) deleted. A dest that already has mosaicWave files (`mosaicWave/`, `libraries/`, `blobs/`, …) is left in place — Save folder does not stash or delete it. Do not pick the library folder as the mount point — that is `{dest}/mosaicWave` after the share is mounted. Failed mounts rmdir only leftover `/Volumes/mosaicWave*` dests — not a folder you browsed. mosaicWave does not `killall Finder` and does not use `/Volumes/Public`. The dest directory must exist before `mount_smbfs` (umount of a `/Volumes` smbfs share deletes that directory; recreate it). The LaunchDaemon plist sets `SessionCreate`. There is no GUI login. A Terminal `sudo mount_smbfs -N` can use the System keychain; the LaunchDaemon cannot, so mosaicWave reads the password once from the System keychain (`security find-internet-password -w`) and passes `//user:pass@host/share`. It does not read the login keychain. Mount specs also try `fileserver._smb._tcp.local`.

`/Volumes` is `root:wheel` 755. Mac debug is **`sudo sh scripts/start.sh`** so uvicorn is root and can `mkdir` + `mount_smbfs` on `/Volumes/mosaicWave-dev-…` (same privilege as a working LaunchDaemon). Do not pre-create a local folder in `/Volumes` and then mount over it as a normal user — that is EPERM. `umount` deletes that directory; the next mount recreates it as root. Do not use Finder `/Volumes/Public`.

`mount_smbfs` uses `-f 0777 -d 0777 -o nostreams,noquarantine,nobrowse`. Mount success is exit 0 plus a listable unique volume — **not** a file create on the share root (`Public/` often forbids files there). Writability is checked later in the library folder `mosaicWave/`.

QNAP SMB/AFP shares **reserve names that start with `.`**. Do not create `.*` probes or temps on the share. Use `mosaicwave-write-test.tmp` and `mw-*.tmp`. `{volume}/.mosaicWave` on QTS is a local volume folder, not an SMB create.

`{data_dir}` = `{mount point}/mosaicWave` (blank mount point → `/Volumes/mosaicWave-fileserver-Public/mosaicWave`). Reboot remounts from `data_dir_smb` at `data_dir_mount` using the same nsmb.conf + System keychain. Settings keeps showing the `smb://` source and the mount point. Username/password stay in nsmb.conf + keychain, not in `config.json`.

Leave the password box blank only after a successful Save folder (the System keychain item already exists). The first Save folder must include the NAS password.

## `config.json`

```json
{
  "data_dir": "/Volumes/mosaicWave-fileserver-Public/mosaicWave",
  "data_dir_smb": "smb://fileserver/Public/mosaicWave",
  "data_dir_mount": "/Volumes/mosaicWave-fileserver-Public"
}
```

- `data_dir` — folder in use now (UNC on Windows, mount + share folder on Mac).
- `data_dir_smb` — durable URL so reboot and the post-Save-folder API restart can remount. Omitted when the library is a local disk / the app folder. If this key is set, start remounts — including leftover `data_dir_smb` next to an app-folder `data_dir`. **Use app folder** is what clears it.
- `data_dir_mount` — share-root dest (browsable). Omitted when blank (then the unique `/Volumes/mosaicWave[-dev]-{host}-{share}` dest is used).
- `MOSAICWAVE_DATA_DIR` still wins (QPKG sets it). If that env is `smb://…`, start remounts the same way. A failed remount of a share library does **not** rewrite `config.json` back to the app folder; the API stays down until the share mounts.
- After a successful NAS Save folder the API **keeps the process and the live mount** (`restarting` stays false). Killing the process to remount is what dropped Mac debug back to the app folder. Local-folder Save folder still restarts. The next cold start remounts from `data_dir_smb` at `data_dir_mount`. Debug `scripts/start.sh` respawns uvicorn if `restart.flag` is present (web stays up). Packaged LaunchDaemon / service KeepAlive does the same.

## What this is not

- Not an SMB FileStore backend (no `source.backend = smb`). After resolve, I/O is local/`LocalFileStore`.
- Not putting NAS passwords in `config.json` or `nsmb.conf` (System keychain + `username=` in nsmb.conf).
- Not replacing QNAP `/share/...` paths on the NAS itself (QPKG already runs on the share volume).
