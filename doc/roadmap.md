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
| **2** | Packaging verify | MSI installed on a test PC; QPKG x86_64 sideloaded on QTS 5; Linux/macOS tarball unpacked on a real host |
| **3** | Backup / restore / export | Library archive of SQLite + FileStore + thumbs (not sync JSON) |
| **4** | ARM64 QPKG | Sideload on a QTS 5 ARM64 NAS |

**Current:** milestone **2** (milestone **1** done).

## Known open items (not a milestone yet)

- Packaged macOS: a Save-folder SMB mount can be torn down by macOS ([smb-paths.md](smb-paths.md))
- Idea, not scheduled: the user's own cloud drive as a FileStore backend ([filestore-later.md](filestore-later.md))
- Next 16 `tsconfig.json` / `next-env.d.ts` local drift (commit or discard)

## Intentionally later or never

- Full Next.js Node runtime as the product
- Google Photos Library/Picker as a full-library sync
- Wrapping Immich as the product
- Matching QuMagie AI on the first release
- Using sync JSON as a library backup
- Multi-device CRDT / posting a whole `sync/changes` blob back
