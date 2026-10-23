# Status

**Updated:** 2026-10-23  
**Keep in sync with:** [`.cursor/rules/mosaicwave-status.mdc`](../.cursor/rules/mosaicwave-status.mdc) (same date, same next step).

Short onboarding snapshot for agents. **Session start:** always read [index.md](index.md) first (For AI Agents + Planning). Names: [naming.md](naming.md).

## Now

**v0.1.1 published** (GitHub pre-release with MSI, QPKG x86_64, posix tarball, `SHA256SUMS.txt`). Repo: https://github.com/yun-innovation/mosaicWave. License FSL-1.1-Apache-2.0. Never work on `main`: branch, push, PR, squash-merge ([process.md](process.md)). Roadmap uses **ordered milestones** ([roadmap.md](roadmap.md)); old Phase 0–9 plan: [roadmap-archive.md](roadmap-archive.md).

**Milestone 1 — done.** **Milestone 2 — done** (packaging verify: MSI, QPKG x86_64, Linux + macOS posix tarballs). **Milestone 3 — done** (i18n and l10n: `en`, `zh-Hant`, `zh-Hans`; [i18n.md](i18n.md)). **Milestone 4 — done** (advanced UI: top bar, sidebar, hash-routed views; [web-ui.md](web-ui.md)). **Milestone 5 — done** (schema maintenance: `library.db` schema v2, ordered upgrade steps; [schema.md § Schema versions](schema.md#schema-versions)). New UI strings must follow `.cursor/rules/i18n.mdc`.

## Next

**Milestone 6 — per-library databases** (foundations and cutover done; share links next; each user's photos and albums in their own SQLite file with their own `rev` clock; share links replace `source_grant`; three PRs, [per-library-db.md](per-library-db.md)). Then 7 backup / restore / export (builds on 6), 8 multilingual API (translated messages and locale date/time in responses), 9 gallery tools (select many, scrubber, search, light theme). 10 ARM64 QPKG is **frozen** (no ARM hardware) and skipped.

## Constraints

- **Multi-platform:** same Python app on QNAP (QPKG), Windows, Linux, and macOS. QPKG is packaging, not the only runtime
- **Python only** as product runtime (no Node, no Mongo daemon)
- Next.js **static export**; TypeScript compiles on the build machine
- **SQLite** for index and later albums
- One **`/api/v1`** for web and mobile
- Originals via **FileStore**; thumbs via **BlobStore**. On Windows MSI: `%PROGRAMDATA%\mosaicWave`; local debug: `%PROGRAMDATA%\mosaicWave-dev`. QNAP `{volume}/.mosaicWave` (survives QPKG Remove); Linux packaged `~/.local/share/mosaicWave`; Linux debug `~/.local/share/mosaicWave-dev`; macOS packaged `/Library/Application Support/mosaicWave`; macOS debug `/Library/Application Support/mosaicWave-dev`. Admin Settings can move the folder (`config.json`). `MOSAICWAVE_DATA_DIR` wins (QPKG sets it). No Google Photos Library/Picker full-library sync
- **Auth:** QNAP users on QTS; local hashed users on standalone (`local_credential`)
- **Authorization:** one private library per user (`source.owner_user_id`) in its own SQLite file (`accounts/{user_id}/library.db`). Admin does not see others’ photos; sharing returns as share links (milestone 6, PR 3)
- **Takeout:** ingest unique files into the caller’s library (`{data_dir}/libraries/{user_id}`); dump may be deleted after import
- Client **offline** = replica of metadata/thumbs; originals stay in FileStore; sync **pull** is `GET /api/v1/sync/changes`. Album **push** is `POST`/`PATCH`/`DELETE /albums` with `base_rev`
- **Milestone complete:** only **`milestone N done`**. Wrap-up (`mosaic done`) does not mark a milestone done

## Read on demand

| Task | Document |
| --- | --- |
| What we are building | [system-overview.md](system-overview.md) |
| How it is split | [architecture.md](architecture.md) |
| Web UI layout / views / URLs | [web-ui.md](web-ui.md) |
| Product / path names | [naming.md](naming.md) |
| Virtual file / blob | [storage.md](storage.md) |
| UNC / `smb://` paths | [smb-paths.md](smb-paths.md) |
| FileStore (originals I/O) | [filestore.md](filestore.md) |
| FileStore later (BYO cloud) | [filestore-later.md](filestore-later.md) |
| How we work | [process.md](process.md) |
| Roadmap (milestones) | [roadmap.md](roadmap.md) |
| Checklists | [progress-todo.md](progress-todo.md) |
| Dated log | [progress-archive.md](progress-archive.md) |
| Takeout import | [takeout.md](takeout.md) |
| Takeout dump (zips / oversized) | [takeout-dump.md](takeout-dump.md) |
| Job workflow / audit | [workflow.md](workflow.md) |
| Schema / `library.db` | [schema.md](schema.md) |
| QPKG / standalone hosts | [qpkg.md](qpkg.md) |
| How to run locally | [README.md](README.md) |
| Postman `/api/v1` tests | [`postman/`](../postman/mosaicwave.postman_collection.json) |
