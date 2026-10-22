# Per-library databases (milestone 6, planned)

Status: **planned, nothing built.** Decided 2026-10-23. Why: the app is meant to be hosted as SaaS later, and every user's photos, albums and change clock should be isolated from every other user's.

## Goal

Each user's photos and albums live in their own SQLite file with their own `rev` clock. The site database holds only accounts, sessions, jobs and share tokens. An existing v2 single-file install migrates without loss. `source_grant` (user-to-user library access) is retired and replaced by share links.

## Layout

```
data/
  library.db                      site DB: user, session, job, job_event, share, schema_history
  accounts/{user_id}/library.db   library DB: source, asset, album, album_asset, schema_history
  libraries/{user_id}/...         originals (unchanged)
  blobs/...                       thumbnails (unchanged)
```

- The library DB is **not** inside `libraries/{user_id}/`, because that folder is scanned as originals.
- Each file has its own `PRAGMA user_version` and `schema_history`, and its own ordered steps. The site DB and the library DB are two upgrade tracks (see [schema.md § Schema versions](schema.md#schema-versions)).
- **Lazy upgrade:** a library DB is upgraded the first time it is opened. A `library.db.pre-v{N}` copy is made first, the same way as the site DB. A restored older library therefore upgrades the same way.
- **`rev`** is `max(rev)+1` inside that library's file. `server_rev` and `since_rev` are per library; a user's change never moves another user's clock.

## Sharing

"Sharing" means social-media style sharing: a link to an album that can be opened without signing in. It is not user-to-user library access.

- A `share` table in the site DB: token, library id, album id, optional expiry, revoked flag.
- A public, read-only route opens that library's DB and serves only that album's thumbnails and originals.
- `source_grant` and its endpoints and UI are removed. Release notes must say user-to-user sharing is gone.

## Three PRs

1. **Plumbing and migration**
   - A per-user engine and session cache, and routing from a request to its library DB. The cache has a size cap and evicts idle engines.
   - Two upgrade tracks (site, library), drift tests for both, and `.cursor/rules/schema.mdc` updated.
   - Schema v3 for the site DB: a one-time split. `library.db.pre-v2` copy first, move each user's rows into their library DB, compare counts, roll back on failure.
   - Tried on a database made by a real v0.1.1 build.
2. **Move the API onto the library DB**
   - Assets, albums, sync pull and push, Takeout, folder scan, thumbs and jobs.
   - Site-wide jobs (Generate thumbs) open each library in turn. The single-running-job rule stays site-wide for now.
   - Response shapes do not change, so the web UI and sync clients keep working; `server_rev` is now per library.
3. **Share links**
   - The `share` table, the public route, and the Share UI; then drop `source_grant`.

## Risks to check early

- The old database has foreign keys from `asset` and `album` to `source` and `user`. Across the file boundary `owner_user_id` becomes a plain id with no foreign key.
- Many users: engine cache size and idle eviction.
- Every code path that joins `user` with `asset` or `album` needs a new home; list them in PR 1.

## Effect on backup (milestone 7)

A site backup is the site DB plus every library folder. A user's backup is one `accounts/{user_id}/` folder plus `libraries/{user_id}/`, and their restore is an exact swap of their own library, so the earlier "additive merge" rules are not needed. The unmerged work in commit `3967fe1` (branch `feature/site-backup-restore`, single-DB site backup and offline restore) is a reference and has to be rebased onto this layout.
