# Progress — to do

Open items only. Roadmap: ordered milestones in [roadmap.md](roadmap.md). The earlier phase checklist is in [progress-archive.md](progress-archive.md) (section "Checklist at pause").

- [x] Triage the Dependabot PRs (no CI: run `pytest` / `npm run build` by hand before merging) — all merged on GitHub
- [x] Release `v0.1.1`: `main` checked, tag pushed, MSI / QPKG x86_64 / tarball built from a clean clone of the tag, SHA-256 sums (`v0.1.0` is a tag only)
- [x] Publish the GitHub pre-release for `v0.1.1` (upload the three packages and `SHA256SUMS.txt`)
- [x] Write the branch / commit policy into `doc/process.md`
- [x] **Milestone 1** — AI rules + doc structure: thin `doc/naming.md`; always-on `docs-before-code.mdc`; `index.md` at session start; process/status use milestones — **done**
- [x] **Milestone 2** — packaging verify: MSI, QPKG x86_64, Linux + macOS posix tarballs — **done**
- [x] Commit Next 16 web config: `tsconfig.json` / `next-env.d.ts`; `proxyClientMaxBodySize`; `output: export` only in production so `next dev` rewrites stay clean
- [x] **Milestone 3** — i18n and l10n: `en`, `zh-Hant`, `zh-Hans`; `user.locale` + `PATCH /api/v1/me`; error `code` + `params`; `i18n-check` before build — **done**
- [x] **Milestone 4** — advanced UI: top bar, account menu, sidebar (slide-over panel on phones), hash-routed views; [web-ui.md](web-ui.md), [PR #24](https://github.com/yun-innovation/mosaicWave/pull/24) — **done**
- [x] **Milestone 5** — schema maintenance: `PRAGMA user_version` + `schema_history`, ordered steps, pre-upgrade copy, newer database refused; [schema.md § Schema versions](schema.md#schema-versions), [PR #27](https://github.com/yun-innovation/mosaicWave/pull/27) — **done**
- [x] **Milestone 6** — per-library databases: one SQLite file per user, site schema v3, grants removed; [per-library-db.md](per-library-db.md), PRs #37 and #38 — **done**
  - [x] PR 1: foundations: two model sets and schema tracks, `accounts_dir`, library-DB manager (no behaviour change)
  - [x] PR 2: cutover: per-request site/library sessions, v2 to v3 migration, API/jobs/sync on the library DB, `source_grant` removed
- [ ] Share links for SaaS (`share` table, public read-only route, Share UI) (**milestone 11, deferred**: only useful when a host is open to the internet)
- [x] Maintenance mode (**milestone 12**; [maintenance.md](maintenance.md)): flag file, `503 maintenance` for non-admins, public status + full-page alert, simplified admin menu, `python -m mosaicwave.maintenance`, start with `MOSAICWAVE_MAINTENANCE=1` (built; not yet marked done)
- [ ] Backup / restore / export (**milestone 7**, four PRs; [backup.md](backup.md))
  - [x] PR 1: site backup (`POST/GET/DELETE /backups`, `python -m mosaicwave.backup`), admin Backup page, offline restore (`python -m mosaicwave.restore`); Generate thumbs moved to Settings; one status bar for job progress on every view; offline restore bumps `thumb_rev` when the archive has no thumbnails ([PR #40](https://github.com/yun-innovation/mosaicWave/pull/40))
  - [x] PR 2: online site restore from the Backup page: stage as a cancellable `site_restore` job, then Restart and apply swaps it in at start-up (`restore-pending.json`, `restore-result.json`); needs maintenance mode
  - [x] PR 3: per-user backup and online exact-swap restore of one library: Settings → My library backup (`library_backup` / `library_restore` jobs, `/me/backups`), restore into any account, previous library kept aside, sync clients re-pull everything; journal recovery at start
  - [ ] PR 4: export selected albums/photos as ordinary files
- [ ] ARM64 QPKG sideload on a QTS 5 ARM64 NAS (**milestone 10, frozen**: no ARM hardware)
- [ ] Multilingual API: language code on requests; translated messages and locale-formatted date/time in responses; sync and ordering timestamps stay UTC; response language and switch target kept separate, tags normalized to canonical codes (**milestone 8**)
- [ ] Gallery tools: select many, timeline scrubber, search (new API), light theme (**milestone 9**)
