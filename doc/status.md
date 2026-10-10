# Status

**Updated:** 2026-10-07  
**Keep in sync with:** [`.cursor/rules/mosaicwave-status.mdc`](../.cursor/rules/mosaicwave-status.mdc) (same date, same next step).

Short onboarding snapshot for agents. Full TOC and commands: [index.md](index.md).

## Now

**v0.1, paused for GitHub migration. The roadmap is being rebuilt** (old Phase 0–9 plan: [roadmap-archive.md](roadmap-archive.md)). No phase is marked done by the reset.

## Next

**Published:** the repo is public at https://github.com/yun-innovation/mosaicWave (single commit, tag `v0.1.0`, license FSL-1.1-Apache-2.0). Never work on `main`: branch, push, PR, squash-merge ([process.md](process.md)). The five Dependabot bumps (Next 16, React, TypeScript 7, `@types/node` 26, react-dom) are merged. Release plan: `v0.1.0` stays as a tag only (no GitHub Release). Version is bumped to `0.1.1`. Left: check `main` with `pytest` and `npm run build`, tag `v0.1.1`, build the assets (MSI, QPKG x86_64, tarball) from a clean clone of that tag under `build/`, and publish a pre-release. Old Azure history stays in a private archive repo and a backup bundle; its plan branch must never go to GitHub. Open items ([progress-todo.md](progress-todo.md)): Linux tarball unpack untested; ARM64 QPKG needs a QTS 5 ARM64 NAS; backup / restore / export not built. Packaged macOS Save-folder SMB limitation: [smb-paths.md](smb-paths.md).

## Constraints

- **Multi-platform:** same Python app on QNAP (QPKG), Windows, Linux, and macOS. QPKG is packaging, not the only runtime
- **Python only** as product runtime (no Node, no Mongo daemon)
- Next.js **static export**; TypeScript compiles on the build machine
- **SQLite** for index and later albums
- One **`/api/v1`** for web and mobile
- Originals via **FileStore**; thumbs via **BlobStore**. On Windows MSI: `%PROGRAMDATA%\mosaicWave`; local debug: `%PROGRAMDATA%\mosaicWave-dev`. QNAP `{volume}/.mosaicWave` (survives QPKG Remove); Linux packaged `~/.local/share/mosaicWave`; Linux debug `~/.local/share/mosaicWave-dev`; macOS packaged `/Library/Application Support/mosaicWave`; macOS debug `/Library/Application Support/mosaicWave-dev`. Admin Settings can move the folder (`config.json`). `MOSAICWAVE_DATA_DIR` wins (QPKG sets it). No Google Photos Library/Picker full-library sync
- **Auth:** QNAP users on QTS; local hashed users on standalone (`local_credential`). Enforced in Phase 5.
- **Authorization:** one private library per user (`source.owner_user_id`) plus owner `source_grant`. Admin does not see others’ photos without a grant
- **Takeout:** ingest unique files into the caller’s library (`{data_dir}/libraries/{user_id}`); dump may be deleted after import
- Client **offline** = replica of metadata/thumbs; originals stay in FileStore; sync **pull** is `GET /api/v1/sync/changes`. Album **push** is `POST`/`PATCH`/`DELETE /albums` with `base_rev`
- **Phase complete:** only **`phase N done`**. Wrap-up (`mosaic done` / `unity done`) does not mark a phase done

## Read on demand

| Task | Document |
| --- | --- |
| What we are building | [system-overview.md](system-overview.md) |
| How it is split | [architecture.md](architecture.md) |
| Virtual file / blob | [storage.md](storage.md) |
| UNC / `smb://` paths | [smb-paths.md](smb-paths.md) |
| FileStore (originals I/O) | [filestore.md](filestore.md) |
| FileStore later (BYO cloud) | [filestore-later.md](filestore-later.md) |
| How we work | [process.md](process.md) |
| Roadmap (being rebuilt) | [roadmap.md](roadmap.md) |
| Checklists | [progress-todo.md](progress-todo.md) |
| Dated log | [progress-archive.md](progress-archive.md) |
| Takeout import | [takeout.md](takeout.md) |
| Takeout dump (zips / oversized) | [takeout-dump.md](takeout-dump.md) |
| Job workflow / audit | [workflow.md](workflow.md) |
| Schema / `library.db` | [schema.md](schema.md) |
| QPKG / standalone hosts | [qpkg.md](qpkg.md) |
| How to run locally | [README.md](README.md) |
| Postman `/api/v1` tests | [`postman/`](../postman/mosaicwave.postman_collection.json) |

Do not read [index.md.sample](index.md.sample) (other project template).
