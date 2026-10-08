# mosaicWave

> **AI-assisted project.** mosaicWave was built with [Cursor](https://cursor.com) and AI assistance, under the direction and review of the maintainer. The code, tests and docs were largely AI-written. Treat it as v0.1 software: review it before exposing it to the internet, and keep your own backups.

A self-hosted, multi-platform photo and video library: a Python API, a static web client, and one `/api/v1` that a future mobile client can share.

## Features

- Gallery with thumbnails, a timeline, a detail view, video playback (play, pause, skip) and albums
- Google Takeout import: zips are read in place, duplicates are removed by SHA-256, and a dump can be pushed from a device
- Local users on a standalone install, QNAP users on QTS; each user has one private library, and an owner can share it with another user
- Metadata sync for offline clients (pull changes, push albums)
- SQLite for metadata; photos stay as ordinary files on disk or a NAS share

## Platforms

| Platform | Package |
| --- | --- |
| Windows | MSI (optional Windows service) |
| QNAP (QTS 5+, x86_64) | QPKG |
| Linux, macOS | Tarball with `install.sh` |

Build and install details: [doc/qpkg.md](doc/qpkg.md).

## Quick start (development)

From the repo root:

- Windows: `scripts\start.cmd`
- Linux: `sh scripts/start.sh`
- macOS: `sudo sh scripts/start.sh` (or `scripts/start.command`)

The API is at http://127.0.0.1:8000/docs and the web UI at http://127.0.0.1:3000. The first run creates the Python virtual environment and runs `npm install`. On first visit you create the admin user. More in [doc/README.md](doc/README.md).

Tests: `cd src/server`, then `.venv/bin/pytest` (macOS/Linux) or `.\.venv\Scripts\pytest` (Windows). `npm run build` in `src/web` must succeed.

## Repository layout

| Path | What |
| --- | --- |
| `src/server` | FastAPI app, library, storage, tests |
| `src/web` | Next.js app (static export) |
| `src/msi`, `src/qpkg`, `src/posix` | Windows, QNAP and Linux/macOS packaging |
| `scripts` | Start, stop, publish and pack scripts |
| `postman` | `/api/v1` Postman collection and environments |
| `doc` | Documentation |

## Status

v0.1. Development is paused after the first feature set; the roadmap is being rebuilt ([doc/roadmap.md](doc/roadmap.md)). Known open items: Linux tarball unpack and the ARM64 QPKG are untested, and backup/restore/export is not built.

## License

mosaicWave is **source-available** under the [Functional Source License, Version 1.1, Apache 2.0 Future License](LICENSE) (`FSL-1.1-ALv2`).

- You may run it for yourself, including inside a business (self-hosting).
- You may not use it to offer a competing product or hosted service, or resell it.
- Each release automatically becomes Apache-2.0 two years after it is published.

This is not an OSI-approved open-source license. The name "mosaicWave" and its logo are reserved and are not covered by the code license. Third-party credits: [NOTICE](NOTICE).

## Contributing and security

Code contributions are not accepted for now; issues are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Documentation

[doc/README.md](doc/README.md) is the entry point; [doc/index.md](doc/index.md) is the full table of contents.
