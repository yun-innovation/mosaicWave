# mosaicWave documentation

> **AI-assisted project.** mosaicWave was built with [Cursor](https://cursor.com) and AI assistance, under the direction and review of the maintainer. The code, tests and these docs were largely AI-written. Treat it as v0.1 software: review it before exposing it to the internet, and keep your own backups.

mosaicWave is a **multi-platform** photo/video library: a **Python API**, a **web client**, and the same API for a future **mobile** client. Install as a **QNAP QPKG** or run **standalone** on Windows or Linux.

These documents are the source of truth for product direction. Implemented schema and FileStore: [schema.md](schema.md), [filestore.md](filestore.md).

**Index (agents + TOC):** [index.md](index.md). **Current phase:** [status.md](status.md).

| Document | What it covers |
| --- | --- |
| [Index](index.md) | TOC plus **For AI Agents** (session start / `mosaic done`) |
| [System overview](system-overview.md) | Product intent, users, what we are (and are not), high-level capabilities |
| [Architecture](architecture.md) | Runtime split, tech stack, hosts, API contract, data and clients |
| [Storage](storage.md) | Virtual FileStore + BlobStore; extra requirements for multi-platform |
| [FileStore](filestore.md) | Originals I/O: virtual paths, LocalFileStore, how scan walks a tree |
| [Process](process.md) | How we design, build, test, and ship (standalone → QPKG) |
| [Roadmap](roadmap.md) | What exists, open items; being rebuilt (old plan: [archive](roadmap-archive.md)) |
| [Takeout import](takeout.md) | Google Takeout → SQLite; hash dedup |
| [Takeout dump](takeout-dump.md) | Download folder: zips, oversized files, JSON, Check folder |
| [Job workflow](workflow.md) | Task runs in SQLite for later audit |
| [Schema](schema.md) | SQLite tables, `library.db` location, media vs metadata, replica and sync |
| [QPKG / hosts](qpkg.md) | Sideload QPKG; `publish.cmd` / `pack.cmd` (`msi` / `qpkg` / `posix`); standalone one-process serve |
| [Postman](../postman/mosaicwave.postman_collection.json) | Importable `/api/v1` collection for manual API tests |

## Decisions already made

- **Multi-platform Python service.** QNAP **QPKG** is a first-class package, not the only runtime. Not a Docker/Immich wrapper as the product.
- **Python (FastAPI)** for the API, indexer, and file access.
- **Next.js with static export** for the web UI. TypeScript is compiled at build time; the browser runs JavaScript. No Node.js as the product runtime.
- **One HTTP API** for web and mobile. Mobile is a client, not a second backend.
- **Virtual file/blob module** ([storage.md](storage.md)): originals via **FileStore**, thumbs via **BlobStore**. v1 backends are local disk (QNAP shares are local paths on the NAS).
- **Photos stay as normal files** on the local FileStore so File Station / SMB / Explorer can still see them. Object storage is a later backend and does not promise that.
- **Google Photos live API dump is out of scope.** Full library import is **Google Takeout** (and later local folder scan), not the Photos Picker/Library APIs.
- **SQLite** for photo metadata and albums. Not MongoDB (extra daemon, weak fit for a single-process install).
- **Auth is a provider:** QNAP users on QTS (no mosaicWave password table on that profile); **local users** on standalone. `/api/v1` still uses a mosaicWave session after the provider accepts the user.
- **Authorization** is app-level: each user has **one private library**. Logged-in is not “see everything.” Admin does not see others’ photos unless the owner shares.
- **Takeout import dedupes** by SHA-256: one `asset` per unique file in a source; album folders add membership only; re-import is idempotent.
- **Google Takeout import** seeds the sample database. Not the Photos Library/Picker APIs.
- **Schema now** ([schema.md](schema.md)): metadata in SQLite, originals in FileStore; client offline replica + sync pull. Job audit ([workflow.md](workflow.md)).

## Local development

From the repo root, **`scripts\start.cmd`** (Windows) or **`sh scripts/start.sh`** (Linux) starts API + web in this session. **On a Mac, debug is root:** `sudo sh scripts/start.sh` (or double-click **`scripts/start.command`**, which re-runs with sudo) so Save folder can `mount_smbfs` under `/Volumes`. **`sudo sh scripts/stop.sh`** stops them. If port **8000** or **3000** is already in use, start **stops those first**, then launches one pair. Debug uses `MOSAICWAVE_PROFILE=dev` (Windows `%PROGRAMDATA%\mosaicWave-dev`, macOS `/Library/Application Support/mosaicWave-dev`, Linux `~/.local/share/mosaicWave-dev`) so it does not share the packaged library. NAS Save folder keeps that live mount; a packaged install on **:8090** is a different process.

| Script | What |
| --- | --- |
| `scripts\start.cmd` / `scripts/start.sh` | Both in this window (`start.command` on a Mac) |
| `scripts\start-api.cmd` / `scripts/start-api.sh` | FastAPI — http://127.0.0.1:8000/docs |
| `scripts\start-web.cmd` / `scripts/start-web.sh` | Next.js — http://127.0.0.1:3000 (`/api` → FastAPI) |
| `scripts\stop.cmd` / `scripts/stop.sh` | Stop API + web (`stop.command` on a Mac) |
| `scripts\stop-api.cmd` / `scripts/stop-api.sh` | Stop FastAPI (port 8000) |
| `scripts\stop-web.cmd` / `scripts/stop-web.sh` | Stop Next.js (port 3000) |
| `scripts\port-who.cmd` / `scripts/port-who.sh` | Show the process on mosaicWave’s listen port (default **8090**; not always) |
| `scripts\port-kill.cmd` / `scripts/port-kill.sh` | Stop that listener (Windows service / LaunchDaemon first if it is running) |
| `scripts\wipe.cmd` / `scripts/wipe.sh` | Wipe this app's own data folder — no `--data-dir`. No flags: wipes both (DB + `libraries/`+`blobs/`); `--db` or `--storage` alone wipes just that one. Packaged: `wipe.cmd` (MSI) / `wipe.sh` or `mosaicWave.sh wipe` (posix tarball, QPKG) |
| `scripts\publish.cmd` | Stage all packages, or one: `msi` / `qpkg` / `posix` |
| `scripts\pack.cmd` | Pack all, or one (`pack.cmd qpkg x86_64`). Same as `pack-msi` / `pack-qpkg` / `pack-posix` |

Same names as `.ps1` if you already have a PowerShell prompt (`.\scripts\start-api.ps1`). First run creates the Python venv and runs `npm install` if needed. Packaged macOS (`/Applications/mosaicWave.app`, port **8090**) is separate; these debug scripts only touch **8000** / **3000**.

Optional env: `MOSAICWAVE_DATA_DIR` (SQLite, FileStore `libraries/`, BlobStore `blobs/`; Windows MSI `%PROGRAMDATA%\mosaicWave`, Windows local debug `%PROGRAMDATA%\mosaicWave-dev`, macOS debug `/Library/Application Support/mosaicWave-dev`; wins over admin Settings). Without that env, admin **Settings** can pick another folder (`config.json` in the default data dir). Also `MOSAICWAVE_PROFILE` (`dev` / `prod`), `MOSAICWAVE_TAKEOUT_DIR` (default Import path), `MOSAICWAVE_PLATFORM` (`standalone` default; `qnap` on QTS), `MOSAICWAVE_SECRET` (session signing; otherwise `{data_dir}/session.key`). First standalone visit creates the admin via **Create admin**. Synthetic Takeout fixture: `src/server/tests/fixtures/takeout`.

`npm run build` in `src/web` must succeed (`output: 'export'`). Python tests: `cd src/server` then `.\.venv\Scripts\pytest` (Windows) or `.venv/bin/pytest` (macOS/Linux).

**Standalone one process** (UI + API on `:8000`): `npm run build` in `src/web`, set `MOSAICWAVE_WEB_ROOT` to `src\web\out`, run uvicorn as in [qpkg.md](qpkg.md). **All packages:** `.\scripts\publish.cmd` then `.\scripts\pack.cmd` (or pass `msi` / `qpkg` / `posix`). **Windows MSI:** `.\scripts\get-winsw.cmd` once — [qpkg.md](qpkg.md). **Linux/macOS:** `publish.cmd posix` then `pack.cmd posix`. QPKG: `.\scripts\get-qdk.cmd` → `tools\` (once), then `pack.cmd qpkg` (`qbuild` in WSL).
