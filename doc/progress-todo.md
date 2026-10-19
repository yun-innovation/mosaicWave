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
- [ ] Advanced UI: top bar and sidebar (**milestone 4**)
- [ ] Schema maintenance: detect the `library.db` schema version, apply upgrade steps in order to the current schema (**milestone 5**)
- [ ] Backup / restore / export (library archive of SQLite + FileStore + thumbs; not sync JSON) (**milestone 6**)
- [ ] ARM64 QPKG sideload on a QTS 5 ARM64 NAS (**milestone 7**)
