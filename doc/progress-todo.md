# Progress — to do

Open items only. The roadmap is being rebuilt ([roadmap.md](roadmap.md)). The earlier phase checklist is in [progress-archive.md](progress-archive.md) (section "Checklist at pause").

- [ ] Triage the Dependabot PRs (no CI: run `pytest` / `npm run build` by hand before merging)
- [ ] Build and attach `v0.1.0` release assets (MSI, QPKG x86_64, tarball) from a clean clone of the tag; SHA-256 sums; mark as pre-release
- [ ] Write the branch / commit policy into `doc/process.md`
- [ ] Linux tarball unpack tested on a real Linux/macOS host
- [ ] ARM64 QPKG sideload on a QTS 5 ARM64 NAS
- [ ] Backup / restore / export (library archive of SQLite + FileStore + thumbs; not sync JSON)
