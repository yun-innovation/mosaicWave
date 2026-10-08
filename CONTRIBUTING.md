# Contributing

mosaicWave is an AI-assisted, source-available project maintained by the maintainers ([LICENSE](LICENSE)). Development is paused at v0.1.

## What is welcome

- **Issues:** bug reports, questions and ideas. Use the issue templates.
- **Pull requests with code:** not accepted for now. The company keeps full copyright so it can run its own hosted service, and accepting outside code would need a contributor license agreement first. If that changes, this file will say so.

## Run the tests

- Server: `cd src/server`, then `.venv/bin/pytest` (macOS/Linux) or `.\.venv\Scripts\pytest` (Windows).
- Web: `npm run build` in `src/web` must succeed.

A few macOS keychain and mount tests in `tests/test_smbpath.py` fail when run on Windows.

## Privacy rules (for issues and logs)

This repo is public. Do not post real hostnames, IP addresses, usernames, emails, passwords, `config.json`, `session.key`, library databases or personal photos or Takeout data. Use fictional values such as host `fileserver`, user `nasuser` and share `Public`. Redact tracebacks that contain your home directory path.

## Commit style (maintainer)

Conventional Commits: `feat:`, `fix:`, `docs:`, `chore:`, `build:`, `test:` with an imperative summary under 72 characters.
