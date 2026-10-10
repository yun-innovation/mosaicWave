# Progress — archive

Dated milestones and decisions. Day-to-day process notes: [process.md](process.md).

| Date | Type | Note |
| --- | --- | --- |
| 2026-08-28 | Decision | Native QPKG; Python FastAPI; Next.js static export; one `/api/v1` |
| 2026-08-28 | Decision | No Node SSR on the NAS |
| 2026-08-28 | Decision | No Google Photos API full-library sync; Takeout later |
| 2026-08-28 | Decision | SQLite for photo index and albums; MongoDB rejected |
| 2026-08-28 | Milestone | Phase 0 docs + agent onboarding (`index.md`, `status.md`, always-on rule) |
| 2026-08-29 | Decision | Auth = QNAP users via QTS session or server-side QNAP check; no mosaicWave password store |
| 2026-08-29 | Decision | Takeout import in Phase 2 to seed sample SQLite; Photos APIs still out of scope |
| 2026-08-29 | Decision | Schema now: files on disk, SQLite metadata, client replica + sync in Phase 6 |
| 2026-08-29 | Milestone | Phase 0 complete — docs, schema, agent onboarding, `src/` layout |
| 2026-08-31 | Decision | Multi-platform runtime (QPKG + Windows/Linux standalone). FileStore + BlobStore required (`storage.md`). Auth: QNAP on QTS, local users on standalone. `source.root_path` → `backend` + `root_uri` |
| 2026-08-31 | Decision | User authorization: `user` + `source_grant` (admin/member, read/write). Not all-users-all-sources. Takeout import dedupes by SHA-256; re-import is idempotent |
| 2026-08-31 | Milestone | Phase 1: FastAPI health + OpenAPI, FileStore/BlobStore + pytest, Next static export, `/api` rewrite, health OK/fail UI |
| 2026-08-31 | Milestone | Local start/stop shortcuts (`scripts/start.cmd`, `scripts/stop.cmd`) — one PowerShell 7 window |
| 2026-08-31 | Milestone | Phase 2: SQLite DDL, FileStore scan, Takeout hash dedup + idempotent re-import, `GET /api/v1/assets`, web list |
| 2026-09-02 | Docs | Takeout download layout + inspect API (`takeout-dump.md`; zip parts, oversized files, metadata.json) |
| 2026-09-02 | Feature | Takeout import reads `takeout-*.zip` in place; unzip not required (`TakeoutZipStore`) |
| 2026-09-02 | Feature | Web Browse… picker for Takeout path (`GET /api/v1/fs/list`) |
| 2026-09-02 | Fix | Takeout import is a background job + progress poll (Next `/api` proxy was resetting the hash request) |
| 2026-09-02 | Decision | Phase complete only on user command `phase N done`; wrap-up does not close a phase |
| 2026-09-02 | Decision | Phase 3 = job workflow/audit (`workflow.md`); gallery becomes Phase 4; sync HTTP is Phase 7 |
| 2026-09-02 | Milestone | Phase 2 complete — library + Takeout zip import (user-tested); next is Phase 3 jobs |
| 2026-09-03 | Docs | Roadmap **Brainstorm** section (not a phase); item 1 = user’s own cloud drive as FileStore |
| 2026-09-03 | Feature | Phase 3 start: `job` / `job_event` persist Takeout import; `GET /api/v1/jobs` |
| 2026-09-03 | Milestone | Phase 3 complete — job audit; next is Phase 4 gallery |
| 2026-09-03 | Milestone | Phase 4 complete — gallery thumbs, timeline, detail; next is Phase 5 auth |
| 2026-09-04 | Milestone | Phase 5 complete — standalone login, `source_grant`, upload, Settings; next is Phase 6 QPKG |
| 2026-09-05 | Milestone | Phase 6 packaging: `publish-qpkg` / `pack-qpkg` (`qbuild` via WSL + QDK `.deb`), LF `qpkg.cfg`, App Center icons, Notification Center + QTS desktop window (`QPKG_DESKTOP_APP`) |
| 2026-09-07 | Fix | QTS `authLogin.cgi` over HTTPS/CGI; HEIC lightbox via `GET …/preview`; video thumbs (`-update 1`, `imageio-ffmpeg`); idle CPU (one ffmpeg, no placeholder retry) |
| 2026-09-07 | Milestone | Phase 6 complete — QPKG + standalone; QNAP sideload (scan share, reboot); next is Phase 7 sync HTTP |
| 2026-09-07 | Feature | `GET /api/v1/sync/changes` metadata pull (`since_rev` / `cursor`; grants + tombstones) |
| 2026-09-08 | Docs | FileStore v1 local done; BYO Drive/OneDrive parked in `filestore-later.md` (not Phase 7) |
| 2026-09-08 | Decision | Phase 7 OpenAPI is a living FastAPI spec, not a freeze; freeze when the product stops, not mid-development |
| 2026-09-08 | Decision | API tests in Postman (`postman/`); no generated OpenAPI client |
| 2026-09-09 | Decision | Takeout dump is not a `source`; inspect uses the selected folder only. Import writes into the caller's private library |
| 2026-09-09 | Decision | Each user has one private library. No one else (including admin) may read or write it until a later share feature. `source_grant` unused; grant APIs 403 |
| 2026-09-09 | Decision | Windows FileStore + BlobStore default under `%PROGRAMDATA%\mosaicWave` (not `%LOCALAPPDATA%`) |
| 2026-09-09 | Feature | Admin Settings can move the data folder; pointer in bootstrap `config.json`; `MOSAICWAVE_DATA_DIR` still wins |
| 2026-09-09 | Feature | Takeout push job: client/mobile uploads dump files; host-path import unchanged |
| 2026-09-09 | Fix | Takeout push streams raw file bytes (`?relative_path=`); Starlette multipart 1MB part cap was HTTP 400 |
| 2026-09-10 | Fix | Takeout push size cap 4GB rejected leftover 4K videos from a 4GB zip split; cap is now 256GB |
| 2026-09-10 | Feature | Manual/virtual albums: `GET/POST /albums`, membership, `GET /assets?album_id=`; same SQLite tables as Takeout |
| 2026-09-10 | Milestone | Device Takeout push user-tested on a large dump |
| 2026-09-10 | Fix | Web gallery: swallow AbortError from Strict Mode remount / album tab (`page.tsx`) |
| 2026-09-10 | Decision | Video playback is a time bar (play / skip). People never see bytes; that is only how the file is fetched |
| 2026-09-10 | Feature | Gallery video player: Play/Pause, clock, −10s/+10s, time bar |
| 2026-09-10 | Milestone | Video time-bar playback user-tested |
| 2026-09-10 | Fix | Drop `proxyClientMaxBodySize` from `next.config.ts` — not in Next 15.5; blocked QPKG `next build` |
| 2026-09-10 | Fix | Next `middlewareClientMaxBodySize` 4GB was too small (same leftover-video case as the push cap); now 256GB |
| 2026-09-11 | Feature | Windows MSI scripts: `publish-msi` / `get-winsw` / `pack-msi` / `install-msi`; data stays in ProgramData on uninstall |
| 2026-09-11 | Feature | `pack-qpkg` with no args builds x86_64 and arm_64; pass an arch for one platform |
| 2026-09-11 | Feature | Product icon: blue mosaic ocean wave (`src/brand/mosaicWave-icon.png`) for QPKG, favicon, and Windows ARP icon |
| 2026-09-11 | Fix | `pack-msi`: WiX 7 harvest cannot Exclude `**` paths; copy `src/msi/shared` (including `.ico`) at pack time |
| 2026-09-11 | Fix | `install-msi` / `uninstall-msi` pass a resolved full path; msiexec cannot open `..\dist\msi\...` |
| 2026-09-12 | Feature | MSI Start Menu + all-users desktop shortcuts open http://127.0.0.1:8000/ |
| 2026-09-12 | Fix | MSI venv is `%PROGRAMDATA%\mosaicWave\runtime` — Program Files is not writable for a normal user |
| 2026-09-12 | Decision | Windows debug data is `%PROGRAMDATA%\mosaicWave-dev`; MSI production is `%PROGRAMDATA%\mosaicWave` |
| 2026-09-12 | Feature | MSI Listen port: WixUI wizard (license → folder → port); silent `PORT=` /qb; shortcuts and service use that port |
| 2026-09-13 | Decision | MSI default Web / API port is **8090** (local debug stays 8000) |
| 2026-09-13 | Decision | ARM QPKG sideload skipped (no ARM NAS). x86_64 QPKG + Windows MSI tested. Next is album push |
| 2026-09-13 | Milestone | Album pull Postman-tested (`GET /api/v1/sync/changes` albums / album_assets) |
| 2026-09-13 | Decision | Phase 8 todo: Linux and macOS packaged deployment (same Python app) |
| 2026-09-14 | Feature | Album push: `POST /albums` optional replica `id`; `PATCH`/`DELETE` `base_rev` → 409 if server `rev` differs |
| 2026-09-14 | Milestone | Simple album push Postman-tested (pull `rev` → PATCH `base_rev`). Sync is not a backup; remaining recipes in schema.md |
| 2026-09-14 | Decision | Phase 8 plan includes product **backup / restore / export** (data dir archive). Sync JSON is never a backup |
| 2026-09-14 | Decision | **Share** is a Phase 8 todo; **backup / restore / export** moved to **Phase 9** (`progress-todo.md`). Sync JSON is never a backup |
| 2026-09-14 | Docs | Postman **Sync replica** folder: create-id, membership `base_rev`, 409 stale, delete + tombstone pull |
| 2026-09-14 | Feature | **Share:** owner `PUT /sources/{id}/grants` (`read`/`write`); no admin bypass; Settings picker |
| 2026-09-14 | Milestone | Share user-tested (owner grant; other user reads/writes that library) |
| 2026-09-14 | Tooling | `scripts/init-db` — create/migrate `library.db` without starting the API (`python -m mosaicwave.initdb`) |
| 2026-09-15 | Decision | QPKG requires QTS **5.0+**. TS-431 (ARMv7 / QTS 4.3.6, arch `arm-x31`) cannot install; `arm_64` is a different CPU |
| 2026-09-15 | Docs | No official QTS ARM VM. Docker `linux/arm64` pytest is wheel smoke only, not App Center |
| 2026-09-15 | Feature | Linux/macOS tarball: `publish-posix` / `pack-posix`, `install.sh`, optional systemd user unit / launchd |
| 2026-09-15 | Tooling | `publish.cmd` / `pack.cmd` dispatch `msi` / `qpkg` / `posix` (no arg = all) |
| 2026-09-16 | Feature | macOS packaged install is `/Applications/mosaicWave.app` + `/Library/Application Support/mosaicWave` + LaunchDaemon |
| 2026-09-16 | Fix | Settings Save folder: skip `.DS_Store`/WAL; checkpoint then byte-copy; rewrite `source.root_uri` on a local tempfile (dest SQLite CANTOPEN rolled back to empty) |
| 2026-09-16 | Tooling | `scripts/port-who` and `port-kill` (port may be 8090, 8000, `web.port`, MSI `PORT`, QPKG `Web_Port`) |
| 2026-09-16 | Fix | `/api/v1/fs/list` on Darwin lists `/Volumes` NAS mounts as the folder owner / console user (root LaunchDaemon cannot `listdir` user SMB) |
| 2026-09-17 | Fix | LaunchDaemon SMB: pass Settings/keychain password as `//user:pass@HOST` (percent-encoded); Terminal `sudo mount_smbfs -N` keychain ACL does not apply to the service |
| 2026-09-17 | Fix | Save folder SMB: require NAS password when Finder already has the share; try `host._smb._tcp.local`; error tells the user to eject if the NAS allows one connection per user |
| 2026-09-17 | Fix | `mount_smbfs` exit 0: keep the volume (umount deletes `/Volumes/mosaicWave-…`); recreate dest between specs; redact `//user:pass@` in errors; do not call a missing mount point an auth failure |
| 2026-09-17 | Fix | LaunchDaemon SMB write: `-f/-d 0777 -o nostreams,noquarantine,nobrowse`; remount if listdir works but create file EPERM; non-dot write probe |
| 2026-09-17 | Fix | QNAP SMB reserved `.` prefix: FileStore/BlobStore temps are `mw-*.tmp`; write probe `mosaicwave-write-test.tmp` |
| 2026-09-17 | Docs | Fictional SMB examples (`fileserver` / `nasuser`); drop lab hostname, personal username, and Takeout library counts from the public tree |
| 2026-09-17 | Fix | Unique SMB mount success is listable `/Volumes/mosaicWave-…`, not a file create on the share root; do not unmount to retry extra specs |
| 2026-09-17 | Fix | Save folder SMB: `//user:pass@HOST/share` first; save nsmb.conf + System keychain only after that works; remount `//HOST/share`; then write-probe the library folder. Stored URL has no `user@` |
| 2026-09-17 | Tooling | `publish-posix`: `npm install` in `src/web` when `node_modules` is missing |
| 2026-09-18 | Fix | macOS/Linux debug data dir is `mosaicWave-dev` (same split as Windows `%PROGRAMDATA%\mosaicWave-dev`) |
| 2026-09-18 | Tooling | macOS/Linux `scripts/start.sh` / `stop.sh` / Finder `.command` (ports 8000/3000), like Windows `start.cmd` |
| 2026-09-18 | Fix | SQLite `NullPool` so overlapping `/assets` + `/sources` sessions do not hit a closed database; Next `app/` icons only (no `public/favicon.ico` / `public/icon.png`) |
| 2026-09-19 | Feature | Settings SMB: keep `smb://` source and a browsable mount point (`data_dir_smb` / `data_dir_mount`); username/password still nsmb.conf + System keychain |
| 2026-09-19 | Fix | Failed SMB remount falls back to the app folder so Settings stays reachable |
| 2026-09-23 | Fix | Save folder NAS keeps the live mount (no API kill); leftover app-folder `library.db` is removed once the dest has it |
| 2026-09-24 | Fix | Boot remount reads System keychain once (no login keychain / host-alias loop that prompted Keychain Access) |
| 2026-09-25 | Fix | Takeout help text: device uploads go to the job tmp folder; Save folder does not copy `{data_dir}/tmp` |
| 2026-09-25 | Fix | Takeout device file/finish close SQLite before streaming or inspecting the dump (NAS `library.db` disk I/O) |
| 2026-10-02 | Fix | `library.db` stays in the app folder; Save folder does not copy SQLite onto SMB |
| 2026-10-02 | Fix | Boot remount skips System keychain find/add when the unique volume is already mounted |
| 2026-10-02 | Fix | Takeout dest names replace SMB-illegal characters (`:`); job error keeps the dump relative path |
| 2026-10-02 | Fix | Receiving-push 409: Click Cancel upload (web FileList cannot resume after reload) |
| 2026-10-02 | Fix | `GET /thumb` JPEG placeholder when SQLite is locked during import |
| 2026-10-03 | Fix | Save folder prepares unique dest (`.DS_Store`, leftover `mosaicWave-*`); mount at the volume not `{volume}/mosaicWave` |
| 2026-10-03 | Fix | Save folder keeps the user Mount point; does not unmount a shared live dest |
| 2026-10-03 | Fix | Leftover mount dest: delete Finder junk only; do not stash mosaicWave files |
| 2026-10-03 | Fix | Settings SMB: host / share / folder; dest is `/Volumes` + mount name + folder |
| 2026-10-03 | Fix | Browse lists as this process only; no GUI-user `/Volumes` retry |
| 2026-10-04 | Feature | `init-db --wipe-storage` (deletes `libraries/`+`blobs/`, separate from `--reset`) |
| 2026-10-04 | Tooling | `init-db` shipped in packages: `init-db.cmd` (MSI), `mosaicWave.sh initdb` (posix tarball + QPKG) |
| 2026-10-05 | Fix | Renamed `init-db` → `init-data`; dropped `--data-dir` (could target the wrong/live folder); no flags now runs a full wipe |
| 2026-10-05 | Fix | Renamed `init-data` → `wipe`; flags `--reset`/`--wipe-storage` → `--db`/`--storage` |
| 2026-10-07 | Decision | Public GitHub release; license **FSL-1.1-Apache-2.0** (source-available: self-host OK, no competing hosted use or resale; becomes Apache-2.0 after 2 years). Issues only, no code contributions |
| 2026-10-07 | Decision | Roadmap reset: old Phase 0–9 plan archived in `roadmap-archive.md`; `progress-todo.md` trimmed to open items; no phase marked done |
| 2026-10-07 | Docs | AI-assisted project notice in `README.md`, `doc/README.md`, `NOTICE`; added `LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `.github` templates and Dependabot |
| 2026-10-07 | Fix | Personal data removal: deleted `exception.rtf`, untracked `.wix` DLLs, fictional `fileserver` in Postman env and a test |
| 2026-10-07 | Decision | Counsel review of the license and name waived by the owner (re-check before SaaS launch). No CI workflow for now |
| 2026-10-07 | Tooling | Private backup bundle of the Azure repo; local orphan branch `public-release` (single commit) for the GitHub push |
| 2026-10-08 | Milestone | Published: `yun-innovation/mosaicWave` on GitHub, single commit plus tag `v0.1.0`, repo made public. Author uses a GitHub no-reply address (GitHub blocked the personal email). Azure remote renamed `azure-archive`; global `http.sslVerify` restored to true |
| 2026-10-09 | Decision | Branch/commit policy written into `process.md`: never work on `main`; branch, PR, squash-merge; Conventional Commits kept as written (guidance, not enforced) |
| 2026-10-09 | Milestone | Dependabot PRs merged: Next 16.3.8, React, TypeScript 7, `@types/node` 26, react-dom. Merged by the owner; Next and React were tested locally, the rest not verified here (no CI). Release assets still to be built from the `v0.1.0` tag |
| 2026-10-10 | Milestone | Version bumped to `0.1.1` (#8); tag `v0.1.1` pushed at `f4697e4`. MSI, QPKG x86_64 and posix tarball built from a clean clone of the tag, with SHA-256 sums; ARM64 QPKG skipped. Personal-string scan of the staged files clean (the only hit was a false positive inside the bundled WinSW binary). Installers not yet run on a test machine; GitHub pre-release not yet published |


---

Historical checklist, moved here from progress-todo.md when the roadmap was reset. Phase numbers refer to [roadmap-archive.md](roadmap-archive.md).

## Checklist at pause (2026-10-07)

Tick when the **exit criteria** for that item are met. Phase order: [roadmap.md](roadmap.md).

## Phase 0 — Foundation

- [x] System overview, architecture, process, roadmap under `doc/`
- [x] Stack locked (FastAPI, Next static export, SQLite, QPKG **and** standalone)
- [x] Agent onboarding: `index.md` For AI Agents, `status.md`, `mosaicwave-status.mdc`
- [x] Schema + offline sync model: `doc/schema.md`
- [x] Virtual file/blob + multi-platform requirements: `doc/storage.md`
- [x] User authorization model (`user` / `source_grant`) and Takeout hash dedup in schema + takeout docs
- [x] `src/` layout exists (`server/`, `web/`, `qpkg/`) even if empty placeholders

## Phase 1 — Local skeleton

- [x] FastAPI `/api/v1/health` and OpenAPI `/docs`
- [x] FileStore + BlobStore local backends and pytest fakes ([storage.md](storage.md))
- [x] Next.js `output: 'export'`; `next build` succeeds
- [x] Dev `/api` rewrite to uvicorn
- [x] Placeholder page shows health OK/fail

## Phase 2 — Library core + Takeout sample DB

- [x] Server SQLite DDL from [schema.md](schema.md) (UUIDs, `rev`, tombstones, `source.backend` / `root_uri`, `user` / `source_grant`)
- [x] One content source (path/env → LocalFileStore)
- [x] Scan image/video extensions into SQLite via FileStore (skip `*.json`)
- [x] Takeout sidecar parse → `taken_at` / `extra_json`
- [x] Takeout albums → `album` / `album_asset`
- [x] Takeout **dedup**: one `asset` per SHA-256; re-import idempotent; fixture with date+album copies
- [x] `POST /api/v1/import/takeout`
- [x] `GET /api/v1/assets` with cursor pagination
- [x] Web list: filename + date; empty and error states
- [x] Tiny fixture or local extract documented; no personal dumps in git
- [x] Implemented schema + FileStore docs (`schema.md`, `filestore.md`)
- [x] Takeout download inspect: zip parts, oversized files, `metadata.json` ([takeout-dump.md](takeout-dump.md))
- [x] Import reads Takeout zips in place (unzip not required)
- [x] Web folder picker for Takeout path (`GET /api/v1/fs/list`)
- [x] Takeout import runs in the background (`GET /api/v1/import/takeout/status`); incremental commit
- [x] Real Takeout download folder imported and tested (zips in place)

## Phase 3 — Job workflow + audit

- [x] `job` table in SQLite (kind, status, times, input/result, progress)
- [x] Takeout import persists a job (survives API restart)
- [x] `GET /api/v1/jobs` and `GET /api/v1/jobs/{id}`
- [x] Web: jobs list for audit; import progress uses job id
- [x] Optional `job_event` audit log

## Phase 4 — Gallery UX (done)

- [x] Python thumbs into BlobStore; `GET .../thumb`
- [x] Timeline grouping
- [x] Detail + original download
- [x] EXIF `taken_at` with mtime fallback

## Phase 5 — Auth and uploads (done)

- [x] Auth provider: QNAP users on QTS; local hashed users on standalone; stub only before Phase 5
- [x] `source_grant`: member cannot list/read ungranted sources; admin can grant
- [x] Upload into a source folder (write grant)
- [x] Settings: add/remove source and grants

## Phase 6 — Host packaging (QPKG + standalone) (done)

- [x] QDK start/stop, data dir, port; `platform=qnap`
- [x] Python bundled or documented dependency
- [x] Exported web packed into QPKG
- [x] Sideload test: scan a share; survives reboot
- [x] Standalone data dir / bind documented (Windows from Phase 1)

## Phase 7 — Mobile-ready API + sync

- [x] OpenAPI published for list/detail/thumb/file/upload/auth/sync (`/docs`; living spec, not frozen)
- [x] Pagination and error shape stable
- [x] `GET /api/v1/sync/changes` (metadata pull into client replica; albums included; Postman-tested)
- [x] Second client can list, fetch a thumb, and apply a sync pull (pytest replica upsert)
- [x] Postman collection for `/api/v1` (`postman/`; import, not a generated client)

## Phase 8 — Product depth

- [x] Virtual albums beyond Takeout (manual albums)
- [x] One private library per user (no share yet; admin cannot see others’ photos)
- [x] Admin Settings: change library data folder (`config.json`; `MOSAICWAVE_DATA_DIR` wins)
- [x] Takeout push from the client (phone / this device / cloud Files) — dump need not be a host path
- [x] Video playback (skip on a time bar; people never see bytes)
- [x] Windows MSI staging/pack scripts (WinSW service; uninstall leaves ProgramData)
- [x] MSI full UI: Web / API port page (default 8090) + optional Windows service; silent `PORT=` / `INSTALLSERVICE=`
- [x] Album push (client replica writes albums back: `base_rev` on PATCH/DELETE; optional create `id`; simple Postman PATCH tested)
- [x] Share (let another user read/write a library; `source_grant`) — user-tested
- [x] Sync Postman beyond simple PATCH: create-`id`, membership `base_rev`, delete + tombstone pull (collection folder **Sync replica**)
- [x] Linux and macOS packaged install (`publish-posix` / `pack-posix` tarball + `install.sh`; optional systemd user unit / launchd). `publish.cmd` / `pack.cmd` dispatch `msi` / `qpkg` / `posix`. macOS `.app` + `/Library/Application Support` + LaunchDaemon in user test; Linux unpack not user-tested.
- [x] macOS Settings Save folder: copy-then-commit; skip Finder junk and SQLite WAL sidecars; rewrite `library.db` on a local tempfile
- [x] Settings App folder vs Library folder; Save folder can return the library to `{bootstrap}` even when that folder still has `session.key` / leftover `library.db`
- [x] macOS remounts a saved `smb://` library (`data_dir_smb` in `config.json`) at LaunchDaemon start after reboot
- [x] Finder `/Volumes` NAS mounts stay with the Mac user; mosaicWave mounts its own `/Volumes/mosaicWave-…` copy because the LaunchDaemon cannot read the user mount
- [x] UNC / `smb://` path handling documented ([smb-paths.md](smb-paths.md))
- [x] `port-who` / `port-kill` (argument, `MOSAICWAVE_PORT`, `web.port`, MSI registry, QPKG `Web_Port`; packaged default 8090)
- [x] Folder picker lists as this process (debug and installer are system services); does not retry as the GUI user
- [x] macOS LaunchDaemon SMB: unique `/Volumes/mosaicWave-…`; connect `//user:pass@HOST/share` (percent-encode `@` `:` `/`); on success write `[HOST] username=` and System keychain (`-A -U`); remount `//HOST/share`; then library write probe. `data_dir_smb` is host-only (`SessionCreate`; no `user@` in the stored URL)
- [x] Save folder prepares the unique dest (`.DS_Store` / leftover `mosaicWave-*`); mount at the volume, not `{volume}/mosaicWave`
- [x] Save folder keeps the user Mount point; does not unmount a dest another process may be using
- [x] Leftover mount dest: delete Finder junk only; do not stash or delete mosaicWave files
- [x] Settings NAS username/password; `PUT /settings` keeps the password on the mount (not in `config.json`); first Save folder must include the NAS password; nsmb/keychain only after a live credentialed mount
- [x] Settings SMB source + browsable mount point (`data_dir_smb` / `data_dir_mount` kept after Save folder; username/password still nsmb.conf + System keychain)
- [x] Settings SMB split: host / share / folder; dest is `/Volumes` + mount name + folder (not pre-filled)
- [x] Save folder skips `{data_dir}/tmp` (Takeout upload leftovers)
- [x] Takeout device upload / finish do not hold SQLite open across the file stream or dump inspect
- [x] `library.db` stays in the app folder (not on SMB); Save folder moves photos/thumbs only
- [x] Takeout dest names replace SMB-illegal characters (`:` and others); job error keeps the dump relative path
- [x] Boot remount does not rewrite the System keychain when the unique volume is already mounted
- [x] Receiving-push 409 tells the web to Click Cancel upload (FileList cannot resume after reload)
- [x] `GET /thumb` returns a JPEG placeholder when SQLite is locked during import
- [x] Device Takeout `Failed to fetch`: clearer connection-dropped message only (do not move tmp)
- [x] macOS/Linux debug start/stop (`scripts/start.sh` / `stop.sh` / Finder `.command`; ports 8000/3000) like Windows `start.cmd`. Mac debug re-execs `sudo` so the API can `mount_smbfs` under `/Volumes`.
- [x] Save folder NAS keeps the live mount (Settings still shows the share, not `/Library/Application Support/mosaicWave-dev`). User-tested on Mac debug `:3000` / `:8000` (`sudo sh scripts/start.sh`): Takeout import into an SMB-mounted folder succeeded.
- [x] Debug data dir `mosaicWave-dev` (macOS `/Library/Application Support/mosaicWave-dev`, Linux `~/.local/share/mosaicWave-dev`; does not share the packaged library)
- [ ] ARM64 QPKG — pack still emits `arm_64`. TS-431 (ARMv7 / QTS 4.3.6) is **not** that arch and cannot run QTS 5; install correctly rejected. Sideload on a QTS 5 ARM64 NAS still open.
- [x] `wipe` (renamed from `init-db`/`init-data`): no `--data-dir` (always this app's own folder, no override footgun); no flags = wipes both DB + `libraries/`+`blobs/`; `--db` / `--storage` alone wipes just one
- [x] `wipe` utility shipped in every package: `wipe.cmd` (MSI), standalone `wipe.sh` + `mosaicWave.sh wipe` (posix tarball + QPKG) — not just the debug scripts

## Phase 9 — Backup / restore / export

- [ ] Backup / restore / export (library archive of SQLite + FileStore + thumbs; not sync JSON)
