# Backup and restore

Milestone 7, four PRs: **site backup + offline restore**, **online site restore from the Backup page**, **per-user backup + online restore of one library**, and **export**. Sync JSON is never a backup ([schema.md](schema.md)). The data it backs up is laid out by milestone 6 ([per-library-db.md](per-library-db.md)).

> **An archive of the whole site contains password hashes** (`local_credential`) and every user's originals. Site backup and restore are **admin only**. Store archives like the data folder itself.

## What a site archive is

One zip, `mosaicwave-site-YYYYMMDD-HHMMSS.zip` (format 1, kind `site`):

| Entry | Content |
| --- | --- |
| `manifest.json` | `format`, `kind`, `created_at`, `app_version`, `schema_version` (site), `library_schema_version` (newest of the library databases), `include_thumbs`, `user_ids`, `libraries` (per user: `schema_version`, `assets`, `albums`), `users`, `assets`, `albums`, `file_count`, `total_bytes`, `warnings` |
| `library.db` | The **site** database (accounts, password hashes, jobs). Consistent snapshot made with the SQLite backup API (safe while the API runs). Running jobs are removed from the copy |
| `accounts/{user_id}/library.db` | One snapshot per user's photos and albums. Each source's `root_uri` is stored as `mw-data:libraries/<id>` so the archive does not depend on the machine's paths |
| `libraries/{user_id}/…` | Every user's originals, byte for byte (stored, not recompressed) |
| `blobs/…` | Thumbnails, only with `--include-thumbs` (they can be regenerated) |

Never in an archive: `session.key`, `config.json`, `nsmb.conf`, `tmp/`, SQLite `-wal`/`-shm` files, `library.db.pre-v*` copies, symlinks. Files the zip cannot hold safely (for example a name starting with a drive letter) are skipped and listed in `warnings`.

The manifest holds counts and byte totals, not a per-file list: the zip's own directory is that list. Archives from before the per-library split (site schema below v3) are not supported.

## Make a backup

- **Web (admin):** the **Backup** page (`#/backup`) starts a backup (optional thumbnails), shows progress, lists the archives with date, size and counts, and downloads or deletes them. It also restores (below). Behind it, `POST /api/v1/backups` starts a job (kind `site_backup`, also visible in Activity). Archives go to `backups/` beside the site database (the app folder; with `MOSAICWAVE_DATA_DIR` set, the data folder), outside the libraries they protect. `GET /api/v1/settings` reports the folder as `backup_dir`. `GET /api/v1/backups` lists them, `GET /api/v1/backups/{name}` downloads, `DELETE` removes. A backup cannot run while another job (import, thumbnails) is running.
- **Command line:** `python -m mosaicwave.backup [--out FOLDER] [--include-thumbs]`. Uses the same data folder as the API. The archive is written as `*.part` and renamed when complete, so a crash never leaves a half archive with the final name.

## Restore online (admin, from the Backup page)

The API keeps the site database and every library database open, so it does not swap them under live requests (open handles on Windows, a stray request re-creating an empty `library.db`, requests in flight). Restore is staged while the server runs and swapped in when it **restarts**, before anything opens.

1. **Turn on maintenance mode** ([maintenance.md](maintenance.md)). Restore is refused otherwise (`409 maintenance_required`), and with a job running (`409 job_running`).
2. **Choose the archive**, the way Import chooses a Takeout. Under **Restore** on the Backup page there are two choices, and **Restore…** on an archive already in the list is a third:
   - **On the server:** a path box with **Browse** (the folder picker in file mode, `GET /api/v1/fs/list?kind=backup`, admin only, lists any `.zip`). `POST /api/v1/backups/restore` `{path}` checks the file (a readable zip with a valid site manifest; `400 backup_path_required`, `backup_not_a_file` or `backup_invalid`, no job started) and stages it **from where it is**, with no copy. Use this for a NAS share or USB drive, and for very large archives.
   - **On this device:** a file picker for a `.zip`; **Upload and restore**. The browser cannot give the server a file's path, so the file is uploaded first with `PUT /api/v1/backups/upload` (raw body, streamed to disk in chunks, with a progress bar and Cancel in the page). The server writes `backups/.upload-<id>.zip`, checks the declared size against free space (`507 backup_no_space`) and the 256 GB cap (`413 upload_too_large`), reads only the manifest and zip index, and stores it as `mosaicwave-site-<time inside the archive>.zip`, so a renamed file works. The same file again is one archive; a different file with the same time gets the next free second. An unusable file is deleted (`400 backup_invalid`). Upload is refused while a restore is staged or a job runs, and leftover `.upload-*` files are removed at start. The archive stays in the list (delete it when you are done), then the page starts the restore as below. It needs about twice the archive size in free space: the upload plus the unpacked copy.
3. **Restore…** on an archive (`POST /api/v1/backups/{name}/restore`), or either choice above, starts a job of kind `site_restore`, shown in the status bar and Activity, and **cancellable**. It checks free space, then unpacks and validates the archive into the same two staging folders the offline restore uses, and checks the databases (all in `stage_site`, shared with the offline command). Nothing live is touched; cancelling or failing removes the staging folders. Progress shows counts only, never file names.
4. When it is done the restore is **staged**: `restore-pending.json` beside the site database records the archive, the stamp, a summary and whether the databases will upgrade. The page shows what the archive holds and offers **Restart and apply** or **Discard**. While a restore is staged, every new job is refused (`409 restore_pending`) and so is moving the data folder, because the staged copy is a snapshot. Whatever an admin changes in their own library meanwhile is lost when the restore is applied.
5. **Restart and apply** (`POST /api/v1/backups/restore/apply`) needs a staged restore, maintenance mode on and no running job. It restarts the API the way **Save folder** in Settings does (`schedule_api_restart`: `restart.flag`, exit after one second; the debug `start.sh` / `start.ps1` respawn it, packaged services keep it alive). Under `uvicorn --reload` or `--workers` the exiting process is only a worker and uvicorn does not replace a dead one (the port would stay open and answer nothing), so the restart also ends the uvicorn parent. Run by hand without a supervisor, start it again yourself. Under pytest nothing restarts and `restarting` is false.
6. **At the next start**, `create_app` calls `apply_pending` before it opens any database. It swaps the staged data in with the same `_swap` as the offline restore (current data kept as `*.before-restore-<time>`, full rollback on failure), writes `restore-result.json` and deletes `restore-pending.json`. A missing or damaged staging folder is an error result and changes nothing. With no staged restore it only removes leftover `.restore-staging-*` folders. Older archives upgrade through the normal schema steps right after, as for offline restore.
7. The page waits for the server (polls `/api/v1/health`, then reloads) and shows the result from `GET /api/v1/backups/restore`: done (with where the previous data was kept, upgrade and thumbnail notes), or the error with nothing replaced. **Dismiss** / `DELETE /api/v1/backups/restore` forgets it. Maintenance stays on; the admin turns it off after checking.

Both state files sit beside the site database, are never archived, and survive the swap. The job row of a restore lives in the old site database and is set aside with it, so the restored site has no record of it; the result file is the record. Sessions are signed cookies keyed by `session.key`, which is not restored, so they stay valid for users that exist in the archive (same ids) and everyone else is signed out. The restored accounts come from the archive: sign in with the administrator the backup was made with.

`GET /api/v1/backups/restore` returns `{pending, result}` (either may be null); all four restore endpoints are admin only.

## Restore (offline, admin)

Also works with no running server, and is what the online restore uses underneath. Stop the API first, then:

```
python -m mosaicwave.restore mosaicwave-site-20261021-093000.zip
python -m mosaicwave.restore ARCHIVE --check     # validate only, reads every byte, changes nothing
```

`--force` skips the check that the API port is free.

The restore is an exact replacement of the site's data:

1. Refuses if `library.db` is in use, the archive is not a mosaicWave site archive, its site or library schema is **newer** than this app, or either folder lacks free space.
2. Extracts into two staging folders, each next to where its data will go: `.restore-staging-app-<time>` beside `library.db` (the site file and `accounts/`, in the app folder) and `.restore-staging-data-<time>` in the data folder (`libraries/` and `blobs/`, which can be a NAS share). Every member name is checked (no `..`, no absolute or drive paths; only `library.db`, `accounts/{id}/library.db`, `libraries/`, `blobs/`), the databases in the archive must match the manifest's `user_ids`, and CRCs are verified while streaming.
3. Checks the staged databases (`integrity_check`, tables present, versions supported), rewrites each `root_uri` to this machine's data folder, and compares asset, album and file counts with the manifest.
4. Moves the current `library.db`, `accounts/`, `libraries/` and `blobs/` aside as `*.before-restore-<time>`, moves the staged data into place, and rolls everything back if any step fails.

An archive from an older app version is upgraded by the normal [schema steps](schema.md#schema-versions) the next time the API starts (site file at startup, each library when first opened, with the usual `library.db.pre-v{N}` copy).

Nothing is deleted: remove the `*.before-restore-*` items yourself once you are satisfied. Thumbnails are not in the archive by default; the server rebuilds each one the first time it is requested, or start the API and use **Generate thumbs** in Settings. Thumbnail URLs carry `thumb_rev` and browsers cache them for an hour, so when the archive has no thumbnails the restore adds the current Unix time to every asset's `thumb_rev` (direct SQL; the library `rev` and `updated_at` do not change, so sync clients see no edit). Browsers then refetch instead of showing stale cached images. With `--include-thumbs` the versions are left alone because the restored files are named after them. Sessions stay valid only if `session.key` is unchanged; users may have to sign in again.

## Per-user backup and restore (any signed-in user, Settings)

Any user, admins included, can back up and restore **their own** library from **Settings → My library backup**. It does not need maintenance mode and never touches anyone else's data.

**Archive.** `mosaicwave-library-YYYYMMDD-HHMMSS.zip`, format 1, `kind: library`: `manifest.json` (source `user_id`, `library_schema_version`, `assets`, `albums`, `file_count`, `total_bytes`, `warnings`, `include_thumbs` false), `library.db` (that user's database with portable `mw-data:libraries/<id>/…` source paths) and `libraries/<user_id>/…` (the originals). No thumbnails (rebuilt on first view) and no password hashes, so it is safe to keep on a laptop. Archives live in `backups/users/<user_id>/`, so a user lists, downloads and deletes only their own, and the admin Backup page, which lists only top-level `mosaicwave-site-*.zip`, never shows them. Wrong kind in either place is `400 backup_wrong_kind`.

**Backup.** `POST /api/v1/me/backups` starts a job (kind `library_backup`, cancellable, in the status bar and Activity; progress counts only). `GET /me/backups` lists with date, size and photo/album counts, `GET /me/backups/{name}` downloads, `DELETE` removes, `PUT /me/backups/upload` takes a zip from the device (same streaming, space and 256 GB checks as the admin upload, shared in `api/v1/archiveupload.py`; named from the time inside the archive). Only one job runs at a time server-wide.

**Restore** (`POST /me/backups/{name}/restore`, job kind `library_restore`) is an exact swap of one library while the server runs, with no restart:

1. **Stage** (`libbackup.stage_library`, cancellable): free-space check, unpack beside the live data (`.restore-staging-user-<time>-<id>` next to the databases, `.restore-staging-userdata-<time>-<id>` in the data folder), check the manifest (newer schema refused, `backup_invalid`), the database (`integrity_check`, tables, counts against the manifest) and every CRC. The database is **remapped to the restoring user**: `root_uri` moves from `libraries/<source id>` to `libraries/<this user>`, every source's `owner_user_id` becomes this user, and the originals are extracted into this user's folder. That is why an archive made by one account restores into another. `thumb_rev` is bumped as for site restore.
2. **Swap** (`libbackup.swap_library`, not cancellable): `LibraryDbs.locked(user_id)` makes this user's requests fail with `503 library_restoring` (`Retry-After: 2`) and closes their engine; the live database is checkpointed, then `accounts/<id>` and `libraries/<id>` are moved aside and the staged ones moved in. If a request still has a file open (Windows cannot rename it) the rename is retried for about ten seconds, then everything is rolled back and the job fails with nothing changed. Other users are never locked.
3. **Previous library deleted** once the new one is in place. Nothing restores from a kept copy, and a user who wants to go back makes a backup first (the Restore confirmation says so). Until then the old library sits in `before-restore/<time>-<id>-db` beside the databases and `before-restore/<time>-<id>-files` in the data folder, which is what a failed swap puts back. It is deleted outside the lock, so a big library does not hold up the user's requests; whatever a crash leaves is removed at the next start. Results show counts only, never server paths.
4. **Sync clients pull everything again.** The archive's `rev` values are older than what clients have seen, so after staging every restored row's `rev` (assets, albums, album membership) is raised by the live library's highest `rev`. Clients ask for changes since a number they hold, so they receive the whole restored library; nothing is skipped. The site restore applies the same shift per user at swap time (`restore.offset_revs`).
5. **Crash safety.** Before moving anything the swap writes `library-swap-<id>.json` beside the site database and deletes it when the new library is in place. At start (`recover_library_swaps`, before any database opens) a leftover journal puts the old library back; leftover staging folders are removed (`remove_stale_user_staging`).

Not covered here: restoring one user out of a site archive, thumbnails inside a user archive, and a cap on how many archives a user may keep (all tied to the storage-space item in [roadmap.md](roadmap.md)).
