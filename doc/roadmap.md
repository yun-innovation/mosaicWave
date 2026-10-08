# Roadmap

**Being rebuilt.** The earlier phased plan (Phases 0–9) is archived in [roadmap-archive.md](roadmap-archive.md). A new roadmap will replace this page.

See [system overview](system-overview.md) for product scope and [architecture](architecture.md) for the stack.

## What exists today (v0.1)

A multi-platform photo/video library: Python API, static web UI, one `/api/v1`.

- Gallery: thumbnails, timeline, detail view, video playback, manual albums
- Google Takeout import (zips read in place, SHA-256 dedup, device upload)
- Local and QNAP users, per-user private library, owner-to-user sharing
- Metadata sync pull and album push for offline clients
- Packages: Windows MSI, QNAP QPKG (x86_64), Linux/macOS tarball

## Known open items

- Linux/macOS tarball unpack is not tested on a real host
- ARM64 QPKG is not tested (needs a QTS 5 ARM64 NAS)
- Packaged macOS: a Save-folder SMB mount can be torn down by macOS ([smb-paths.md](smb-paths.md))
- Backup / restore / export of the library is not built (sync JSON is never a backup)
- Idea, not scheduled: the user's own cloud drive as a FileStore backend ([filestore-later.md](filestore-later.md))

## Intentionally later or never

- Full Next.js Node runtime as the product
- Google Photos Library/Picker as a full-library sync
- Wrapping Immich as the product
- Matching QuMagie AI on the first release
- Using sync JSON as a library backup
- Multi-device CRDT / posting a whole `sync/changes` blob back
