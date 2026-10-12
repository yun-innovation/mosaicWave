# Progress — to do

Open items only. Roadmap: ordered milestones in [roadmap.md](roadmap.md). The earlier phase checklist is in [progress-archive.md](progress-archive.md) (section "Checklist at pause").

- [x] Triage the Dependabot PRs (no CI: run `pytest` / `npm run build` by hand before merging) — all merged on GitHub
- [x] Release `v0.1.1`: `main` checked, tag pushed, MSI / QPKG x86_64 / tarball built from a clean clone of the tag, SHA-256 sums (`v0.1.0` is a tag only)
- [x] Publish the GitHub pre-release for `v0.1.1` (upload the three packages and `SHA256SUMS.txt`)
- [x] Write the branch / commit policy into `doc/process.md`
- [ ] **Milestone 1** — AI rules + doc structure: thin `doc/naming.md`; finish always-on docs-before-code; align remaining rules (session start already forces `doc/index.md`)
- [ ] Install the `v0.1.1` MSI on a test PC; sideload the x86_64 QPKG on a QTS 5 NAS (**milestone 2**)
- [ ] Linux tarball unpack tested on a real Linux/macOS host (**milestone 2**)
- [ ] Commit the Next 16 `tsconfig.json` / `next-env.d.ts` changes (or discard them)
- [ ] Backup / restore / export (library archive of SQLite + FileStore + thumbs; not sync JSON) (**milestone 3**)
- [ ] ARM64 QPKG sideload on a QTS 5 ARM64 NAS (**milestone 4**)
