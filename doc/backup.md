# Backup and restore

Milestone 7. Three parts, each its own PR: **site backup + offline restore** (this page, shipped first), **per-user backup + online restore + Backup view**, and **export**. Sync JSON is never a backup ([schema.md](schema.md)). The data it backs up is laid out by milestone 6 ([per-library-db.md](per-library-db.md)).

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

- **Web (admin):** the **Backup** page (`#/backup`) starts a backup (optional thumbnails), shows progress, lists the archives with date, size and counts, and downloads or deletes them. It also shows the exact restore command for the newest archive. Behind it, `POST /api/v1/backups` starts a job (kind `site_backup`, also visible in Activity). Archives go to `backups/` beside the site database (the app folder; with `MOSAICWAVE_DATA_DIR` set, the data folder), outside the libraries they protect. `GET /api/v1/settings` reports the folder as `backup_dir`. `GET /api/v1/backups` lists them, `GET /api/v1/backups/{name}` downloads, `DELETE` removes. A backup cannot run while another job (import, thumbnails) is running.
- **Command line:** `python -m mosaicwave.backup [--out FOLDER] [--include-thumbs]`. Uses the same data folder as the API. The archive is written as `*.part` and renamed when complete, so a crash never leaves a half archive with the final name.

## Restore (offline, admin)

Stop the API first, then:

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

Nothing is deleted: remove the `*.before-restore-*` items yourself once you are satisfied. Thumbnails are not in the archive by default; start the API and use **Generate thumbs** in Settings. Sessions stay valid only if `session.key` is unchanged; users may have to sign in again.

## Per-user backup and restore (next PR)

A normal user can download a backup of **their own** library (their `accounts/{id}/library.db` and `libraries/{id}/`). Restoring it is an **exact swap** of that one library while the API runs: it waits until no job is running, closes the user's database, swaps both folders, and keeps the previous copy aside. Other users are not touched, and the archive can be restored into a different account.
