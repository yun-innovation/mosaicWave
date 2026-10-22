# Per-library databases (milestone 6, planned)

Status: **planned, nothing built.** Decided 2026-10-23. Why: the app is meant to be hosted as SaaS later, and every user's photos, albums and change clock should be isolated from every other user's.

## Goal

Each user's photos and albums live in their own SQLite file with their own `rev` clock. The site database holds only accounts, sessions, jobs and share tokens. An existing v2 single-file install migrates without loss. `source_grant` (user-to-user library access) is retired and replaced by share links.

## Layout

```
{db folder}/                      the folder that holds library.db today (Settings.database_path.parent)
  library.db                      site DB: user, local_credential, job, job_event, share, schema_history
  accounts/{user_id}/library.db   library DB: source, asset, album, album_asset, schema_history
{data folder}/
  libraries/{user_id}/...         originals (unchanged)
  blobs/...                       thumbnails (unchanged)
```

- **The SQLite files stay where `library.db` is today, not in the data folder.** The data folder can be an SMB share and SQLite is unsafe there, so `config.py` keeps the database in the app folder (`bootstrap_dir`) unless `MOSAICWAVE_DATA_DIR` is set (QPKG), when both folders are the same. The library DBs follow the same rule through a new `Settings.accounts_dir = database_path.parent / "accounts"`. This corrects the first draft of this page, which put them under `data/`.
- The library DB is **not** inside `libraries/{user_id}/`, because that folder is scanned as originals.
- A user's full state is therefore two folders, possibly on different volumes: `accounts/{user_id}/` and `libraries/{user_id}/`. Backup and restore (milestone 7) must handle both.
- Each file has its own `PRAGMA user_version` and `schema_history`, and its own ordered steps. The site DB and the library DB are two upgrade tracks (see [schema.md § Schema versions](schema.md#schema-versions)).
- **Lazy upgrade:** a library DB is upgraded the first time it is opened. A `library.db.pre-v{N}` copy is made first, the same way as the site DB. A restored older library therefore upgrades the same way.
- **`rev`** is `max(rev)+1` inside that library's file. `server_rev` and `since_rev` are per library; a user's change never moves another user's clock.

## Sharing

"Sharing" means social-media style sharing: a link to an album that can be opened without signing in. It is not user-to-user library access.

- A `share` table in the site DB: token, library id, album id, optional expiry, revoked flag.
- A public, read-only route opens that library's DB and serves only that album's thumbnails and originals.
- `source_grant` and its endpoints and UI are removed. Release notes must say user-to-user sharing is gone.

## Three PRs

The migration runs at startup, so the API must already read from the library DBs once it has moved the rows. Plumbing and cutover therefore cannot be two shippable PRs; the split is foundations (no behaviour change), then one atomic cutover, then share links.

1. **Foundations (no behaviour change)**
   - Two model sets (`SiteBase`, `LibraryBase`), two step lists and two `ensure_schema` tracks, with drift tests for both and `.cursor/rules/schema.mdc` updated.
   - `Settings.accounts_dir`, and a library-DB manager (engine cache with a size cap, lazy `ensure_schema` on first open, dispose-all).
   - The running app still uses the single database, so existing installs and tests behave as before.
2. **Cutover**
   - Per-request site and library sessions; `ImportJobRunner`, Takeout, scan, thumbs and sync on the library DB.
   - Schema v3 migration with `library.db.pre-v2` first, count checks, rollback, and a migration report that lists skipped orphans.
   - `source_grant` removed: table, `GET`/`PUT /sources/{id}/grants`, `granted_source_ids` in `/auth/status`, and the grants UI. User-to-user sharing is gone from this PR until PR 3.
   - `config.py` data-folder moves cover `accounts/`; a library's root is computed, not rewritten.
   - `GET /sync/changes` treats `since_rev > server_rev` as a full pull.
   - Tried on a database made by a real v0.1.1 build, and on a multi-user database.
3. **Share links**
   - The `share` table in the site DB, the public read-only route, and the Share UI.
## Risks to check early

- The old database has foreign keys from `asset` and `album` to `source` and `user`. Across the file boundary `owner_user_id` becomes a plain id with no foreign key.
- Many users: engine cache size and idle eviction.
- Every code path that joins `user` with `asset` or `album` needs a new home; list them in PR 1.

## Effect on backup (milestone 7)

A site backup is the site DB plus every library folder. A user's backup is one `accounts/{user_id}/` folder plus `libraries/{user_id}/`, and their restore is an exact swap of their own library, so the earlier "additive merge" rules are not needed. The unmerged work in commit `3967fe1` (branch `feature/site-backup-restore`, single-DB site backup and offline restore) is a reference and has to be rebased onto this layout.

## Findings: how the code splits (read-only pass for PR 1)

Models today: `user`, `local_credential`, `source`, `source_grant`, `job`, `job_event`, `asset`, `album`, `album_asset`, plus `schema_history`. One engine, one `session_factory` on `app.state`.

### Table by table

| Table | Goes to | Notes |
| --- | --- | --- |
| `user`, `local_credential` | site DB | Auth, `/auth`, `/users`, settings. No change in logic |
| `job`, `job_event` | site DB | Progress is written by a runner thread. `job.user_id` stays a real foreign key (same file) |
| `share` (new) | site DB | PR 3 |
| `source` | library DB | One live row per library. `owner_user_id` becomes a plain id (no foreign key across files); `source.id` stays, since asset ids, album ids and URLs use it |
| `asset`, `album`, `album_asset` | library DB | `library/albums.py`, `query.py`, `sync.py`, `takeout.py`, `thumbs.py` only touch these (plus `source`), never `user`, so they work unchanged on a library session |
| `source_grant` | removed | Cannot work across files. Replaced by share links |
| `schema_history` | both | One per file |

### Seams that need real work

1. **Two sessions per request.** Today `get_session` yields one session and `library.py` (1107 lines, about 40 uses of `User`) mixes users, sources and assets. It needs a site session (auth) and a library session for `user.id` (data). `require_user_brief` and the Takeout upload path open sessions by hand.
2. **`ImportJobRunner`** (11 places open `self._factory()`): job rows go to the site DB, assets and albums to the user's library DB. The runner needs the site factory plus a library-session getter keyed by `user_id`. `recover_interrupted_jobs` stays on the site DB.
3. **`auth/access.py`** (`granted_source_ids`, `grant_perm`, grant replace/list) and `ownership.py`: access becomes "your own library"; `GET`/`PUT /sources/{id}/grants`, `granted_source_ids` in `/auth/status`, and the grants UI in `SettingsPanel.tsx` go away.
4. **`config.py` data-folder moves** (`copy_data_dir`, `switch_data_dir`, `_rewrite_source_root_uris`, `recover_library_db_to_bootstrap`, `clear_bootstrap_library_payload`, `data_dir_occupied`) are written around one `library.db`. They need to cover `accounts/`. Better: compute a library's root from the data folder and the owner id instead of rewriting `source.root_uri` in every file.
5. **Schema layer.** One `Base`, one `CURRENT_VERSION`, one `steps.py`, and a drift test. This becomes two metadata sets and two step lists, with `ensure_schema` called per file.
6. **`runtime.py` / `db.py`:** `make_engine` becomes a site engine plus a cache of library engines (they use `NullPool`, so an engine is only a cheap object; cap the cache and dispose all of them in `dispose_runtime`).
7. **`sync.py`:** `max_sync_rev` and `next_sync_rev` already compute `max(rev)+1` over the three tables, so inside a library file the clock is per library with no code change. `album_cond` includes `Album.source_id IS NULL` (legacy rows visible to everyone); in a per-user file that must go.

### Migration (site v2 to v3)

For each user: create `accounts/{id}/library.db` at the current library schema, copy their `source` row and every `asset`, `album`, `album_asset` row with that `source_id`, then compare counts. Make `library.db.pre-v2` first; roll back (delete the new files, keep the old tables) on any failure. Then drop the moved tables from the site DB.

- **Orphans:** `album` rows with `source_id IS NULL` and `source` rows with no owner belong to nobody. Report them in the migration notes and do not copy them (they are not reachable through the albums API today).
- **Existing `rev` values are kept.** A client that stored `since_rev` from the old global clock may now be ahead of its library's `server_rev`. `GET /sync/changes` must treat `since_rev > server_rev` as `0` (full pull).
- **Open data-folder moves** must not run while the migration runs; it happens at startup, before the API serves.
