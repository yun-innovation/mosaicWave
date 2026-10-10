# Progress — to do

Open items only. The roadmap is being rebuilt ([roadmap.md](roadmap.md)). The earlier phase checklist is in [progress-archive.md](progress-archive.md) (section "Checklist at pause").

- [x] Triage the Dependabot PRs (no CI: run `pytest` / `npm run build` by hand before merging) — all merged on GitHub
- [ ] Release `v0.1.1`: check `main`, tag, build MSI / QPKG x86_64 / tarball from a clean clone of the tag, SHA-256 sums, GitHub pre-release (`v0.1.0` is a tag only)
- [x] Write the branch / commit policy into `doc/process.md`
- [ ] Linux tarball unpack tested on a real Linux/macOS host
- [ ] ARM64 QPKG sideload on a QTS 5 ARM64 NAS
- [ ] Backup / restore / export (library archive of SQLite + FileStore + thumbs; not sync JSON)
