# Google Takeout import

**Priority:** Phase 2 (right after the API skeleton). Used to **seed SQLite** with a real library (dates, GPS, album names) without Google Photos APIs.

Live Google Photos Library/Picker sync remains **out of scope**. Takeout is a **folder of files + JSON sidecars** the user already downloaded from [takeout.google.com](https://takeout.google.com/). Download layout (zips, oversized videos, JSON): [takeout-dump.md](takeout-dump.md).

## Goal

1. Point mosaicWave at the **Takeout download folder you selected** (the directory that contains the zips, or an extracted tree). mosaicWave **reads** that folder — [takeout-dump.md](takeout-dump.md). Unzip is not required. The dump is **not** a library source; it is only the import job’s input and may be deleted afterward.
2. **Copy** each unique original into the **caller’s private library** (`{data_dir}/libraries/{user_id}`). SQLite stores metadata and `relative_path` on **that** source. Do **not** swallow binaries into SQLite. The import `dest` field is ignored.
3. Fill `asset` (and `album` / `album_asset`) per [schema.md](schema.md). Serving `GET .../file` uses the destination `LocalFileStore`.
4. **No duplicate assets.** The same photo in a year folder and an album folder is **one** `asset` plus album membership. Re-running import on the same dump does not create new rows.

Live layout is **the destination folder’s filenames**, like QuMagie content sources. Do not use Google dump folders, `YYYY/MM`, or hash directories as the library tree. Hash sharding belongs in **BlobStore** keys only.

## Layout we accept

Typical Photos export:

```
Google Photos/
  Photos from 2024/
    IMG_1234.jpg
    IMG_1234.jpg.json          # sidecar — skip as a photo
  Trip to Kyoto/
    IMG_1234.jpg               # often a second copy of the same bytes
    IMG_1234.jpg.json
    metadata.json              # album folder metadata, if present
```

- Index **image/video** extensions only.
- Never treat `*.json` as an asset.
- `taken_at`: sidecar `photoTakenTime.timestamp` if present, else EXIF, else file mtime.
- GPS / caption / extras: sidecar → `asset.extra_json` (and columns later if we query them often).
- **Albums:** folder name (except year buckets `Photos from YYYY` / `YYYY的相片`) → `album` + `album_asset`. Folder `metadata.json` is album metadata, not a photo.

## Dedup (required in Phase 2)

Takeout **copies** the same shot into a date folder and each album folder. mosaicWave must **not** show two timeline items for one photo.

### Identity

- **Dedupe key:** SHA-256 of file bytes (`asset.content_hash`). Required for every Takeout media file (not optional, not size+mtime).
- Scope: **the caller's library** (`source.owner_user_id`). Do not merge with another user's files.
- Unique in SQLite: `(source_id, content_hash)` where hash is not null.

### One walk, one asset

1. Walk all media via FileStore. Skip `*.json`.
2. Hash the file. Classify the parent folder:
   - **Date folder:** `Photos from YYYY` or locale year buckets such as `YYYY的相片` / `YYYY相簿` → not an album.
   - **Album folder:** anything else under the Takeout photos root → album.
3. If this hash is **new** in the library: **copy** bytes into that folder (`unused_relative` + original filename, with SMB-illegal characters such as `:` replaced) and insert `asset`. Copy failures keep the dump relative path on the job (`error` and `current`).
4. If this hash **already exists**: do **not** insert another `asset` or copy again (unless the library file is missing). If the dump folder is an album, ensure `album` + `album_asset` only.
5. Do not treat a `.jpg` and a related `.mp4` (Live/motion) as the same asset; different bytes, different hashes.

`GET .../file` streams the library copy. Extra Takeout copies stay in the dump until the user deletes the dump; they are not second library rows. Import does **not** tombstone library assets because they are absent from a later dump walk.

### Idempotent re-import

`POST /api/v1/import/takeout` into the **same user’s library** must be safe to run again:

| Already in DB | Import does |
| --- | --- |
| Same `(source_id, content_hash)` | Reuse `asset.id`. Update metadata only if sidecar/EXIF is richer or fields changed; bump `rev` only then |
| Same `(album_id, asset_id)` | Leave `album_asset`; do not insert a second membership |
| File gone from the **library FileStore** | Tombstone on **folder scan** of that source — not because the Takeout dump was deleted |
| File gone from a later dump walk | Leave the library asset; dump is not the source of truth |

A second import of the **same dump** must not change timeline cardinality (same asset count, same album memberships) except for photos **newly present** in that dump.

### Tests (fixture, not a personal dump)

Under `src/server/tests/fixtures/`: two copies of one tiny JPEG (date folder + album folder) + sidecars.

- First import: **1** `asset`, **1** `album`, **1** `album_asset`.
- Second import: same ids and counts.
- Timeline/list API returns one row for that photo.

Plain **folder scan** (non-Takeout `kind`) stays **one row per path** unless we later add optional hash-merge. Dedup is a Takeout import rule because Google duplicates on purpose.

How Google lays out the **download** (zips, oversized videos, `metadata.json`): [takeout-dump.md](takeout-dump.md).

## API

| Method | Path | Role |
| --- | --- | --- |
| POST | `/api/v1/import/takeout/inspect` | Verify a **host** download folder (zip parts, oversized files, sidecars). Reads zip directories; does not require unzip |
| POST | `/api/v1/import/takeout` | Host dump `path` only. Copies unique files into the caller's library; dump may then be deleted |
| POST | `/api/v1/import/takeout/push` | Start a client-upload job (phone, browser, Files/Drive app). Same ingest; dump never has to sit on the mosaicWave host |
| POST | `/api/v1/import/takeout/push/{id}/file` | Query `relative_path` (percent-encoded), raw body. Oversized leftover videos may exceed the Takeout zip split (often 4GB) |
| POST | `/api/v1/import/takeout/push/{id}/finish` | Inspect uploaded files and run the usual hash-dedup import; staging is deleted |
| POST | `/api/v1/import/takeout/push/{id}/cancel` | Abort while files are still arriving |
| GET | `/api/v1/import/takeout/status` | Progress (`processed` / `total` / `current`) and final counts |

Same `GET /api/v1/assets` as a plain folder scan. Files added under `{data_dir}/libraries/{user_id}` appear on the next **scan**. Hashing a large zip dump can take hours; the Next.js `/api` rewrite must not hold that work (it resets). In `next dev`, Takeout push and other large bodies go to uvicorn `:8000` directly (Starlette’s multipart parser caps parts at 1MB). Commits every 25 files.

## Client / mobile push

The host-path job (`POST /import/takeout` with `path`) needs a folder the **API process** can read. Phones cannot offer that.

**Push import** is a second job of the same kind (`takeout_import`, `mode=push`):

1. Client starts a session (`POST /import/takeout/push`).
2. Client uploads each dump file (zips, oversized videos, extracted tree) as `POST .../file?relative_path=` with the file bytes as the body. Files land in `{data_dir}/tmp/takeout-push/{job_id}` (one folder per upload session). SQLite is closed before the file body is read (and before finish inspects the dump). `library.db` stays in the app folder, not on the NAS.
3. Client calls **finish**. The server runs the same SHA-256 ingest into the caller's library, then deletes that job’s staging dir.

Save folder does not copy `{data_dir}/tmp`.

The dump can live on the phone, a laptop, or a cloud folder the **client** can read (Files app, iCloud Drive, Google Drive app). mosaicWave does **not** OAuth to Drive/OneDrive for this — that is later FileStore work ([filestore-later.md](filestore-later.md)). Bytes still transit `/api/v1` into FileStore; unique originals stay on the mosaicWave host, as with host-path import.

Refresh or closing the web page does not cancel a push job. The folder picker on the web cannot resume after refresh (the browser drops the FileList). A 409 while a receiving push is still running tells the user to **Click Cancel upload**, then pick again. Keep the native app alive if the upload is in progress.

Staging writes `{data_dir}/tmp/takeout-push/{job_id}` with pathlib (not FileStore). `_fs_part` replaces Windows-illegal characters only on NT. On Mac/Linux an extracted-tree upload onto NAS tmp can still fail on `:` in a host filename. Zip **member** names stay virtual until library copy (`safe_library_filename`).

## What we will not do

- Call Google Photos APIs or feed Takeout URLs/IDs into the Picker.
- Keep serving originals from the Takeout dump or zip after import.
- Require the dump to remain on disk for the gallery to work.
- Require the full multi-dozen-GB archive to develop: a **small extract** (tens of files) is the sample dataset. Do not commit personal Takeout dumps to git.
- Block import on hashing video (hash in a stream; slow is OK for Phase 2).

## Dev sample

Use a local path, e.g. env `MOSAICWAVE_TAKEOUT_DIR`, gitignored. Optional tiny **synthetic** fixture under `src/server/tests/fixtures/` (fake jpeg + sidecar, **including a duplicate pair**) for pytest — not a real Google dump.

## Implementation order

1. Phase 1 — health API + Next export + **FileStore/BlobStore** ([storage.md](storage.md)).
2. Phase 2 — folder scan **and** this importer so SQLite is not empty (importer uses FileStore + **hash dedup**).
3. Phase 3 — persist each import as a **job** for audit ([workflow.md](workflow.md)).
