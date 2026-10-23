# Roadmap

Work is tracked as **ordered milestones**, not the old Phase 0–9 plan (archived in [roadmap-archive.md](roadmap-archive.md)).

See [system overview](system-overview.md) for product scope and [architecture](architecture.md) for the stack. Gate language: only **`milestone N done`** marks a milestone complete ([index.md](index.md)).

## What exists today (v0.1.1)

A multi-platform photo/video library: Python API, static web UI, one `/api/v1`. Pre-release packages are on GitHub ([releases](https://github.com/yun-innovation/mosaicWave/releases)).

- Gallery: thumbnails, timeline, detail view, video playback, manual albums
- Google Takeout import (zips read in place, SHA-256 dedup, device upload)
- Local and QNAP users, per-user private library, owner-to-user sharing
- Metadata sync pull and album push for offline clients
- Packages: Windows MSI, QNAP QPKG (x86_64), Linux/macOS tarball

## Ordered milestones

| # | Milestone | Exit when |
| --- | --- | --- |
| **1** | AI rules + doc structure | Always-on rules force `index.md` at session start and docs-before-code; thin [naming.md](naming.md); status/index use milestones (not phases); status **Next** is one task |
| **2** | Packaging verify | MSI installed on a test PC; QPKG x86_64 sideloaded on QTS 5; Linux/macOS tarball unpacked on a real host — **done** |
| **3** | i18n and l10n | UI and user-facing strings can be translated; at least one locale besides the default — **done** |
| **4** | Advanced UI | Immich-style chrome: top bar and sidebar (not wrapping Immich); one view at a time with hash URLs; sidebar becomes a slide-over panel on phones — **done** |
| **5** | Schema maintenance | `library.db` records its schema version. At startup the server detects the version (older databases without one are identified from their tables and columns), applies each upgrade step in order until it matches the current schema, and refuses a database newer than the app — **done** |
| **6** | Per-library databases | Each user's photos and albums live in their own SQLite file (`accounts/{user_id}/library.db`) with their own `rev` clock; the site DB keeps only accounts and jobs. A v2 single-file install migrates without loss; `source_grant` is removed. Two PRs: foundations (no behaviour change), cutover (migration, API, `source_grant` removed) ([per-library-db.md](per-library-db.md)). Share links moved to milestone 11 (deferred) — **done** |
| **7** | Backup / restore / export | Site backup (site DB + every library folder) and per-user backup (one library folder), each restored by an exact swap; export of selected albums/photos as ordinary files; restoring an older archive upgrades it through the schema steps. Three PRs: site backup + offline restore ([backup.md](backup.md)), per-user backup + online restore + Backup view, export. Builds on milestone 6 |
| **8** | Multilingual API | A client sends a language code (`en`, `zh-Hant`, `zh-Hans`; else the user's saved `user.locale`, then `en`) and `/api/v1` answers with translated messages, including error text, Takeout warnings, and job errors and progress text, plus dates and times formatted for that language. Timestamps used for sync and ordering (`rev`, `updated_at`, `taken_at` and the like) stay ISO 8601 in UTC next to any formatted text, so existing clients keep working. The per-request response language and the target language of the language switch (`PATCH /api/v1/me`) are separate inputs: neither sets the other, API tags are normalized to the UI's canonical codes, and after a switch the next response is in the new language ([i18n.md § API language](i18n.md#api-language-milestone-8-planned)) |
| **9** | Gallery tools | Select several photos at once (add to album, download); timeline scrubber to jump by month and year; search by filename and date (needs a new API); light theme toggle. Each can ship as its own PR |
| **10** | ARM64 QPKG | Sideload on a QTS 5 ARM64 NAS — **frozen** (no ARM hardware for testing; skip until some is available) |
| **11** | Share links (SaaS) | An owner creates a read-only link to an album or library (`share` table in the site DB: token, library id, album id, optional expiry, revoked flag), a public route serves it without login, and a Share UI manages links — **deferred**: only useful when a host is reachable from the internet; skip until mosaicWave is offered as a hosted service |

**Current:** milestone **7** (milestones **1**–**6** done). Milestones **10** (frozen) and **11** (deferred, SaaS) are skipped when picking the next task.

## Known open items (not a milestone yet)

- Packaged macOS: a Save-folder SMB mount can be torn down by macOS ([smb-paths.md](smb-paths.md))
- Idea, not scheduled: the user's own cloud drive as a FileStore backend ([filestore-later.md](filestore-later.md))

## Intentionally later or never

- Full Next.js Node runtime as the product
- Google Photos Library/Picker as a full-library sync
- Wrapping Immich as the product
- Matching QuMagie AI on the first release
- Using sync JSON as a library backup
- Multi-device CRDT / posting a whole `sync/changes` blob back
