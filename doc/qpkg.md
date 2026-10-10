# QPKG and standalone hosts

How to run the **same Python app** as a QNAP package or as a standalone process. Product runtime is Python only (no Node). Web UI is a Next.js **static export** served by FastAPI.

Sideload on a NAS is the Phase 6 exit test; this file is the procedure. Architecture tree: [architecture.md](architecture.md) QPKG packaging.

## Standalone (Windows / Linux / macOS)

Same API as the QPKG. Default `MOSAICWAVE_PLATFORM=standalone` (local hashed users).

| Env | Meaning | Default |
| --- | --- | --- |
| `MOSAICWAVE_DATA_DIR` | FileStore `libraries/`, BlobStore `blobs/`, and (when this env is set) SQLite `library.db` | Windows **MSI:** `%PROGRAMDATA%\mosaicWave`; **local debug** (`scripts\start.cmd`): `%PROGRAMDATA%\mosaicWave-dev`; Linux packaged: `~/.local/share/mosaicWave`; Linux debug: `~/.local/share/mosaicWave-dev`; macOS **packaged:** `/Library/Application Support/mosaicWave`; macOS **debug:** `/Library/Application Support/mosaicWave-dev`. Wins over Settings `config.json`. QPKG start sets this to `{volume}/.mosaicWave` (outside Install_Path so App Center Remove does not wipe photos). Settings folder picker is then read-only. Without this env, `library.db` stays in the app folder when Save folder points `{data_dir}` at a NAS |
| `MOSAICWAVE_PROFILE` | Bootstrap folder. Windows: `dev` → `mosaicWave-dev`, `prod` → `mosaicWave`. macOS: `dev` → `/Library/Application Support/mosaicWave-dev`, `prod` → `/Library/Application Support/mosaicWave`. Linux: `dev` → `~/.local/share/mosaicWave-dev`, otherwise `~/.local/share/mosaicWave`. MSI / LaunchDaemon set `prod`. Unset: packaged install is prod, otherwise `dev` | unset |
| `MOSAICWAVE_PLATFORM` | `standalone` or `qnap` | `standalone` |
| `MOSAICWAVE_WEB_ROOT` | Directory of `next build` export (`out/`) | unset (API only; use `next dev` in development) |
| `MOSAICWAVE_HOST` | Bind address | `127.0.0.1` |
| `MOSAICWAVE_PORT` | Bind port | `8000` |
| `MOSAICWAVE_SECRET` | Session HMAC | `{data_dir}/session.key` if unset |
| `MOSAICWAVE_TAKEOUT_DIR` | Default Takeout inspect/import path | unset |

**Development** (two processes): [README.md](README.md) `scripts\start.cmd` / `scripts/start.sh` — FastAPI `:8000`, Next `:3000`. **Mac debug:** `sudo sh scripts/start.sh` (API is root so `/Volumes` SMB mounts work; Next stays the invoking user so `src/web/.next` is not root-owned). Finder: `scripts/start.command`. If `publish-posix` hits `EACCES` on `.next`, `sudo rm -rf src/web/.next` and retry.

**One process** (packaged-style, after `npm run build` in `src/web`):

```text
set MOSAICWAVE_WEB_ROOT=<repo>\src\web\out
cd src\server
.venv\Scripts\uvicorn mosaicwave.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000/ — UI and `/api/v1` on the same origin. First visit: **Create admin**. Each user gets one private library. Import Takeout into it.

### Shortcuts (`publish.cmd` / `pack.cmd`)

From the repo root:

```text
.\scripts\publish.cmd
.\scripts\pack.cmd
```

That runs every `publish-*` / `pack-*` (MSI, QPKG, Linux/macOS). One target: `.\scripts\publish.cmd msi` (or `qpkg`, `posix`) and the matching `pack.cmd`. QPKG arch still works: `.\scripts\pack.cmd qpkg x86_64`, or `.\scripts\pack.cmd x86_64` (all targets; the arch goes to `pack-qpkg`). The `publish-msi.cmd` / `pack-msi.cmd` (and qpkg/posix) names still work.

### Windows MSI (`publish-msi` / `pack-msi`)

Same app as the one-process serve: static web + uvicorn, `platform=standalone`, library in `%PROGRAMDATA%\mosaicWave` (not inside Program Files). Uninstall removes the program and, if present, the Windows service; **it does not delete ProgramData**. Local `scripts\start.cmd` uses `%PROGRAMDATA%\mosaicWave-dev` so debug does not share the MSI library.

From the repo root **on Windows**:

```text
.\scripts\publish.cmd msi
.\scripts\get-winsw.cmd
.\scripts\pack.cmd msi
```

Same as `publish-msi.cmd` / `pack-msi.cmd`.

Once per machine: Python **3.10+** on PATH (`py -3` or `python`), and WiX CLI (`dotnet tool install --global wix`). WiX 7 asks you to accept the OSMF EULA once (`wix eula accept wix7`); `pack-msi` also passes `--acceptEula` so a fresh install still packs. `get-winsw` saves `tools\WinSW-x64.exe` (service wrapper).

`pack-msi` writes `dist\msi\mosaicWave_0.1.1_x64.msi`. Install on **this PC** (Administrator):

```text
.\scripts\install-msi.cmd
.\scripts\install-msi.cmd 8090
```

The script resolves `dist\msi\mosaicWave_0.1.1_x64.msi` to a full path (`msiexec` cannot open `..\`). Double-click the `.msi` (or `install-msi.cmd` with **no** argument) for the full wizard: license → install folder → **Web / API port** (same origin, default **8090**) and **Install Windows service** (on by default) → Install. Silent: `msiexec /i mosaicWave_0.1.1_x64.msi INSTALLSERVICE=1 /qb` (add `PORT=8100` to override). `INSTALLSERVICE=0` is files only (Start Menu **Start mosaicWave**). Start Menu and desktop URL shortcuts use that port (`http://127.0.0.1:<port>/`). After install, use the shortcut and **Create admin**. Remove: `.\scripts\uninstall-msi.cmd` or Settings → Apps. Uncheck the service only if you will start `mosaicWave-run.cmd` yourself.

Upgrade: install the new `.msi` over the old one (`MajorUpgrade`). Photos stay in ProgramData.

First service start creates a venv in `%PROGRAMDATA%\mosaicWave\runtime` (not Program Files) and `pip install`s (needs Python 3.10+ and PyPI). That can take a few minutes. Do not run `mosaicWave-run.cmd` from Program Files as a normal user — start the **mosaicWave** service, or use the Start Menu shortcut after the service is running. Uninstall leaves ProgramData (library **and** `runtime`); that is not a photo wipe.

### Linux and macOS (`publish-posix` / `pack-posix`)

Same one-process app as the MSI: static web + uvicorn, `platform=standalone`. Python **3.10+** is a host dependency (venv under the data dir `runtime/`, not inside the app prefix). Uninstall does **not** delete the library.

From the repo root:

```text
.\scripts\publish.cmd posix
.\scripts\pack.cmd posix
```

Same as `publish-posix.cmd` / `pack-posix.cmd`. On Linux/macOS: `sh scripts/publish-posix.sh` then `sh scripts/pack-posix.sh`. Output: `dist/posix/mosaicWave_0.1.1_posix.tar.gz`. Node.js is a **build host** dependency (`npm install` in `src/web` when `node_modules` is missing); the install target does not need Node.

On the target (Python 3.10+ on PATH):

```sh
tar xf mosaicWave_0.1.1_posix.tar.gz
cd mosaicWave
sudo sh install.sh
```

**macOS** (like the Windows MSI): app in `/Applications/mosaicWave.app`, library in `/Library/Application Support/mosaicWave` (machine-wide, same role as `%PROGRAMDATA%`). Local debug (`sudo sh scripts/start.sh`) uses `/Library/Application Support/mosaicWave-dev` (same parent, `-dev` suffix) with the API as **root** so Save folder can mount `/Volumes`. First debug start creates that data folder (`root:admin` `2775`). `sudo sh install.sh` installs a **LaunchDaemon** (disable with `--no-launchd`). After install, open http://127.0.0.1:8090/ — do not run `mosaicWave.sh start` as a normal user (that folder is root-owned). Manual start: `sudo /Applications/mosaicWave.app/mosaicWave.sh start`. If you already had a library under `~/Library/Application Support/mosaicWave`, that first sudo install copies it into the machine-wide folder. Python **3.10+** on PATH. Uninstall does **not** delete the library: `sudo sh /Applications/mosaicWave.app/uninstall.sh`. Settings and Browse `smb://` / UNC: [smb-paths.md](smb-paths.md). The service mounts its own `/Volumes/mosaicWave-…` (a second SMB session). First Save folder: `//user:pass@HOST/share` (escaped) → nsmb.conf `[HOST]` + System keychain → remount `//HOST/share` → write-probe the library folder. Store `smb://server/share/folder` with no `user@`.

**Linux** default is still per-user (`~/.local/lib/mosaicWave`, data `~/.local/share/mosaicWave`): `sh install.sh` then `mosaicWave start`. `scripts/start.sh` uses `~/.local/share/mosaicWave-dev`. Optional `sh install.sh --systemd`. `sh install.sh --prefix /opt/mosaicWave` needs write access.

Default listen **127.0.0.1:8090**. The port is **not fixed**: `MOSAICWAVE_PORT`, `{data_dir}/web.port` (written on start), the MSI **PORT** page (`HKLM\SOFTWARE\mosaicWave`), QPKG `Web_Port`, or an argument to the helpers below. Local debug `scripts\start.cmd` uses **8000**.

Show or free the listener (repo, or next to `mosaicWave.sh` after install):

```text
.\scripts\port-who.cmd
.\scripts\port-kill.cmd
.\scripts\port-who.cmd 8000
```

```sh
sh port-who.sh
sudo sh port-kill.sh
```

On macOS a LaunchDaemon will respawn uvicorn unless you `sudo` the kill script (or `mosaicWave.sh stop`). Do not set `MOSAICWAVE_DATA_DIR` if you want Settings to move the folder.

Create/migrate `library.db` without the debug venv (same tables as API startup; does not start uvicorn):

```text
wipe.cmd
wipe.cmd --db
wipe.cmd --storage
```

```sh
mosaicWave.sh wipe
mosaicWave.sh wipe --db
mosaicWave.sh wipe --storage
```

`wipe.cmd` is next to `mosaicWave-run.cmd` in the installed MSI folder. The posix tarball and QPKG also ship a standalone `wipe.sh` next to `mosaicWave.sh` — `./wipe.sh [--db] [--storage]` is the same as `./mosaicWave.sh wipe [--db] [--storage]`. No flags: wipes both (`library.db` **and** `libraries/`+`blobs/`), then an empty schema — stop the service first. One flag alone wipes just that: `--db` (DB only) or `--storage` (originals + thumbs only). There is no `--data-dir`; it always targets this install's own data folder. Debug equivalent: `scripts\wipe.cmd` / `scripts/wipe.sh` ([schema.md](schema.md)).

## QPKG

Templates live in `src/qpkg/`. The start script sets `MOSAICWAVE_PLATFORM=qnap` (QNAP accounts only; no local password table), data dir `{volume}/.mosaicWave`, and serves the exported web from `$QPKG_ROOT/web`.

Python is **not bundled**. App Center **requires** the **Python 3** QPKG (or **QPython312**) **before** mosaicWave will install. That interpreter must be **3.10+** (official QNAP Python 3.12 is at `$Python3/opt/python3/bin/python3`).

Install order: Python 3 → mosaicWave. **Install** (not Enable) creates the venv and `pip install`s wheels onto the **data volume** (`$QPKG_ROOT/tmp`, not QTS `/tmp`). If pip cannot reach PyPI, install **fails** with a message pointing at `mosaicWave.log`. Enable then only starts uvicorn (no `nohup`; waits until the configured port listens). Default port is **8090** (`QPKG_WEB_PORT` / `Web_Port` in `/etc/config/qpkg.conf`). If that port is taken, install/start picks the next free port and records it (QTS desktop iframe follows `Web_Port`). To set it yourself:

```sh
/share/CACHEDEV1_DATA/.qpkg/mosaicWave/mosaicWave.sh port 8100
/share/CACHEDEV1_DATA/.qpkg/mosaicWave/mosaicWave.sh restart
```

Then open `http://<nas>:<port>/`. Current value: `mosaicWave.sh port` or `/sbin/getcfg mosaicWave Web_Port -f /etc/config/qpkg.conf`. `mosaicWave.sh wipe` (or standalone `wipe.sh`) works the same way (`{volume}/.mosaicWave`, not `$QPKG_ROOT`). `pillow-heif` is optional in practice if the wheel fails; thumbs degrade. Video thumbs use `ffmpeg` on PATH, a QPKG copy, or the `imageio-ffmpeg` wheel (preferred over QTS `/usr/bin/ffmpeg`). Failed thumbs are not re-decoded on every request; ffmpeg runs one at a time with `-threads 1`. `QPKG_DESKTOP_APP=1` opens the UI in a **QTS desktop window** (not a new browser tab). After install, the icon is in the **Main menu**; drag it onto the QTS desktop to pin a shortcut. App Center shows install progress; QTS **Notification Center** (and the system log) report success or failure. Install can take a few minutes while pip runs (`QPKG_TIMEOUT` covers a slow Enable if deps were skipped).

### Publish staging (`publish-qpkg`)

From the repo root **on Windows**:

```text
.\scripts\publish.cmd qpkg
```

Same as `publish-qpkg.cmd` / `publish-qpkg.ps1`. On Linux: `sh scripts/publish-qpkg.sh`. This runs `next build`, copies `src/web/out` and `mosaicwave/` into `dist/qpkg/mosaicWave/` (gitignored), and writes LF line endings for the shell scripts.

That folder is a **QDK tree**, not an installable `.qpkg`. Next step is `pack-qpkg` (`qbuild`).

`qpkg.cfg` and the start script must be **LF** (no CR). If `QPKG_NAME`/`QPKG_VER` include a carriage return, `qbuild` names the file `mosaicWave<CR>_0.1.1<CR>_x86_64.qpkg`. Windows shows that CR as a box (U+F00D). QDK also looks for `icons/mosaicWave.png` using that same name, so **App Center gets no icon**. `publish-qpkg` / `pack-qpkg` strip CR; `.gitattributes` keeps `src/qpkg` on LF.

App Center icons are `src/qpkg/icons/mosaicWave.png` (64×64), `mosaicWave_80.png` (80×80), and `mosaicWave_gray.png` (disabled) — the blue mosaic wave mark. Source: `src/brand/mosaicWave-icon.png`; regenerate with `python scripts/export-icons.py`. QDK copies them into the package as `.qpkg_icon*.gif`. Web favicon / Apple touch / Windows `mosaicWave.ico` (Add/Remove Programs) / macOS `src/posix/mosaicWave.icns` (Finder `.app`) come from the same export.

### Install QDK (once, WSL)

`qbuild` is Linux-only. `pack-qpkg.cmd` runs it through **WSL**. Install the QDK **Debian package** inside WSL — not the `.qpkg` (that one is for App Center on the NAS).

Releases: [github.com/qnap-dev/QDK/releases](https://github.com/qnap-dev/QDK/releases). Current documented version is **v2.5.3**; if a newer tag exists, use that tag and matching filenames.

1. On Windows, from the repo root, download the `.deb` into `tools\` (gitignored):

```text
.\scripts\get-qdk.cmd
```

Default is **amd64** (`qdk_2.5.3_amd64.deb`), matching a typical Windows PC and Ubuntu WSL. Pass `arm64` or `armhf` if `uname -m` in WSL is `aarch64` or `armv7l`. `/force` re-downloads.

2. Install that file **inside WSL** (not in `cmd`). Official pattern from the [QDK README](https://github.com/qnap-dev/QDK) — `apt-get install` a **local** `.deb`:

```sh
sudo apt-get update
sudo apt-get install -y /mnt/d/mosaicWave/tools/qdk_2.5.3_amd64.deb
qbuild --help
```

`get-qdk.cmd` prints the `wslpath` for this repo if WSL is available. Do not run `apt-get` in Windows `cmd`/`pwsh`.

### Pack (`.qpkg`) — `pack-qpkg` runs `qbuild`

After staging exists and QDK is installed in WSL, from the repo root **on Windows**:

```text
.\scripts\pack.cmd qpkg
.\scripts\pack.cmd qpkg x86_64
.\scripts\pack.cmd qpkg arm_64
```

Same as `pack-qpkg.cmd`. No extra arch packs **every shipped arch** (`x86_64` and `arm_64`). Pass a QDK arch to pack only that platform (`x86_64`, `arm_64`; aliases `x64` / `arm64` also work). On Linux/NAS: `sh scripts/pack-qpkg.sh` or `sh scripts/pack-qpkg.sh x86_64`.

The `.qpkg` files land under `build/` in that folder (`dist/qpkg/mosaicWave/build/`). Default pack writes `mosaicWave_0.1.1_x86_64.qpkg` and `mosaicWave_0.1.1_arm_64.qpkg`.

### Pack on the NAS (optional)

Install **QDK** from App Center (the `QDK_*.qpkg` asset on the same releases page). Copy `dist\qpkg\mosaicWave` to a share (or run `publish-qpkg.sh` on the NAS), SSH in, `cd` to the repo, run `sh scripts/pack-qpkg.sh`.

### Sideload (Install Manually)

App Center → **Install Manually** → choose the `.qpkg` on **this computer** (the PC whose browser is open to App Center), not a file already on the NAS. After `pack-qpkg` pick the file that matches the NAS CPU: `mosaicWave_0.1.1_x86_64.qpkg` or `mosaicWave_0.1.1_arm_64.qpkg` under `dist/qpkg/mosaicWave/build/`. No extra character before the underscores.

If an earlier install used a broken filename, **Remove** that package first, then Install Manually again. The library is **not** in the QPKG folder: it lives at `{volume}/.mosaicWave` (example `/share/CACHEDEV1_DATA/.mosaicWave`). Remove/reinstall keeps that folder. To wipe photos, delete `.mosaicWave` yourself (File Station: show hidden). Watch App Center finish (progress) and the QTS notification for success or failure. **Enable**, then open mosaicWave from the **Main menu** (or drag that icon to the desktop). Sign in with a QNAP account (or a QTS session cookie). Import Takeout from a folder on the NAS. Confirm **stop / start / reboot** still serves the gallery.

### Which `.qpkg` / which NAS

App Center **Install Manually** must get a file whose **CPU** and **QTS version** both match. Otherwise the log is: `Failed to install mosaicWave … Installation package is incompatible. Use the correct package.`

| NAS CPU (example) | QDK arch | File |
| --- | --- | --- |
| Intel/AMD 64-bit | `x86_64` | `mosaicWave_0.1.1_x86_64.qpkg` |
| ARM 64-bit (A53/A55/A57, “arm_64”) | `arm_64` | `mosaicWave_0.1.1_arm_64.qpkg` |
| ARM 32-bit Alpine (TS-x31 / TS-x31P2, “arm-x31” / “arm-x41”) | `arm-x31` or `arm-x41` | not in the default pack; `.\scripts\pack-qpkg.cmd arm-x31` |

`qpkg.cfg` sets **`QTS_MINI_VERSION=5.0.0`**. QTS **4.3.x cannot install** this package, even with the right CPU name.

**TS-431** (ARMv7 rev1, firmware **4.3.6.2805**): last QTS for that model is 4.3.6. It cannot run QTS 5. Arch is **arm-x31**, not `arm_64`. **Not a supported mosaicWave host** (needs QTS 5.0+ and Python 3.10+). Use a QTS 5 NAS, or run standalone on a PC.

Do not assume “ARM NAS” → `arm_64`. 32-bit ARM (`uname -m` → `armv7l`) is a different package.

### Without an ARM NAS

There is **no official QTS ARM VM**. You cannot App Center–install, exercise `authLogin.cgi`, or prove BusyBox start on this PC.

What you **can** do on an x86 Windows/WSL box (Docker Desktop with QEMU, or `binfmt`):

```text
docker run --rm --platform linux/arm64 -v /mnt/d/mosaicWave/src/server:/src -w /src python:3.12-bookworm bash -lc "pip install -e '.[dev]' && pytest -q"
```

That checks **aarch64 wheels** (Pillow, pillow-heif, imageio-ffmpeg, FastAPI). It is not a QPKG or QTS 5 test. `qbuild --build-arch arm_64` on WSL only stamps the package CPU; it still does not run QTS.

A cheap ARM64 Linux VM (cloud Ampere/Graviton, or a Pi) can run the **standalone** app the same way. Sideload on a **QTS 5 ARM64 NAS** remains the real host check.

`pack-qpkg` already emits both CPU packages. Sideload the matching file; do not assume one Python wheel fits both (HEIF / ffmpeg thumbs can fail on ARM even when the QPKG installs). ARM64 QTS 5 start/stop/gallery is still a Phase 8 host check. 32-bit ARM + QTS 4.3 is out of scope.

### Logs and QuLog

Uvicorn stdout/stderr (startup, HTTP, tracebacks) is **not** in QuLog. It is a file next to the QPKG:

```text
$(/sbin/getcfg mosaicWave Install_Path -f /etc/config/qpkg.conf)/mosaicWave.log
```

Typical path: `/share/CACHEDEV1_DATA/.qpkg/mosaicWave/mosaicWave.log` (`CACHEDEV1_DATA` follows the volume you installed on). SSH or File Station (enable hidden `.qpkg` if needed).

**QuLog Center** (Control Panel → QuLog Center → **Event Log**) already sees mosaicWave **lifecycle** lines written with `/sbin/log_tool`: install, missing Python, start/stop, start failure. Those are QTS system events, not the HTTP log.

Do **not** pipe `mosaicWave.log` into QuLog. Access logs would flood Event Log and are not a documented QuLog app API. Keep the file for debug; keep `log_tool` for events operators should see. A later option is `logger -t mosaicWave` (syslog) if you want the same events under Syslog in QuLog — still not the uvicorn file.

### QTS login

`POST /api/v1/auth/login` with a QTS `NAS_SID` cookie (empty body) or with NAS username/password. The web UI tries the cookie first when `platform=qnap`.

The server talks to QTS `authLogin.cgi` on **localhost** using the System web port and HTTPS (Force SSL / stunnel), then GET and POST, then falls back to running `authLogin.cgi` from `/home/httpd/cgi-bin` or `/usr/local/apache/cgi-bin` if Apache is not bound on 127.0.0.1. Override the CGI URL with env `MOSAICWAVE_QNAP_CGI` if needed. Passwords are sent base64-encoded the way QTS expects.

### HTTPS (document only)

mosaicWave binds **HTTP** (QPKG default `0.0.0.0:8090`, Windows MSI `127.0.0.1:8090`, local debug `127.0.0.1:8000`). It does not terminate TLS.

- **QTS:** Use the NAS certificate via Force SSL / stunnel on the QTS web port, or a reverse proxy QPKG (e.g. nginx, Caddy) that forwards to mosaicWave’s `Web_Port`.
- **Standalone:** Put Caddy or nginx in front if the API is on the LAN. `MOSAICWAVE_HOST=0.0.0.0` only after that (or firewall) is in place.

