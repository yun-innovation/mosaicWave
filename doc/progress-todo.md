# Progress — to do

Open items only. The roadmap is being rebuilt ([roadmap.md](roadmap.md)). The earlier phase checklist is in [progress-archive.md](progress-archive.md) (section "Checklist at pause").

- [x] Triage the Dependabot PRs (no CI: run `pytest` / `npm run build` by hand before merging) — all merged on GitHub
- [x] Release `v0.1.1`: `main` checked, tag pushed, MSI / QPKG x86_64 / tarball built from a clean clone of the tag, SHA-256 sums (`v0.1.0` is a tag only)
- [ ] Publish the GitHub pre-release for `v0.1.1` (upload the three packages and `SHA256SUMS.txt`)
- [ ] Install the `v0.1.1` MSI on a test PC; sideload the x86_64 QPKG on a QTS 5 NAS
- [ ] Commit the Next 16 `tsconfig.json` / `next-env.d.ts` changes (or discard them)
- [x] Write the branch / commit policy into `doc/process.md`
- [ ] Linux tarball unpack tested on a real Linux/macOS host
- [ ] ARM64 QPKG sideload on a QTS 5 ARM64 NAS
- [ ] Backup / restore / export (library archive of SQLite + FileStore + thumbs; not sync JSON)
