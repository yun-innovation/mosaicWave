# Google Takeout download layout

Google does **not** give you one folder of photos. A Photos Takeout is a **download directory**: numbered zip archives, and sometimes huge videos sitting next to those zips.

Point mosaicWave at the **folder you selected** — the directory that **contains** `takeout-*.zip` (Google usually names it `20260831T100949Z`). Do not point at a parent that only wraps that folder. **On this server:** Browse… lists folders on the **host** (`GET /api/v1/fs/list`), not the browser computer. On QNAP, Browse starts at `/share`. **On this device / mobile:** the client uploads that folder via `POST /import/takeout/push` (the dump does not need to be copied to the NAS first). Import opens the zip central directories, walks photos inside the archives, and attaches oversized files that Google left beside the zips.

Related: [takeout.md](takeout.md) (dedup and API), [filestore.md](filestore.md).

## What Google puts in the download folder

```
some-folder/                               ← any name; not a mosaicWave source
  20260831T100949Z/                        ← select this (the folder that contains the zips)
    takeout-…-1-001.zip
    takeout-…-1-003.zip                    ← ~2 GB splits (size you chose on takeout.google.com)
    takeout-…-001.zip                      ← often tiny: archive_browser.html only
    some-video-002.mov                     ← larger than the zip size; not a failed download
```

Inside the zips (mosaicWave reads this without extracting):

```
Takeout/
  archive_browser.html
  Google Photos/   or   Google 相片/
    Photos from 2024/   or   2024的相片/
      IMG_1234.jpg
      IMG_1234.jpg.json                    ← per-file sidecar
    Trip to Kyoto/
      metadata.json                        ← album folder metadata
      IMG_1234.jpg                         ← extra copy of the same bytes
```

| What you see | What it is |
| --- | --- |
| `takeout-*.zip` | Split archive. mosaicWave **reads them in place**. |
| Loose `.mov` / `.mp4` next to the zips | File larger than the archive size. Name usually ends with `-NNN` matching a **missing** zip number. mosaicWave maps it next to the sidecar that is still inside a zip. Client **push** accepts these (size cap is 256GB, not the zip split) |
| `metadata.json` | Album title hints. **Not** a photo. Album **name** is the folder name (v1). |
| `{file}.json` or `{file}.supplemental-metadata.json` | Per-photo sidecar (`taken_at`, GPS). Never an asset. |
| Year folder `Photos from YYYY`, `YYYY的相片` | Date bucket. Not an album. |
| Any other folder under Google Photos | Album. Same bytes in year + album folder → one `asset` after SHA-256 dedup. |

An extracted `Takeout\` folder is **optional**. If zips are present, import uses the zips (so a partial unzip does not matter).

## Oversized files

If you chose 2 GB zip splits, a 7 GB video cannot go in a zip. Google leaves it beside the archives and still puts the `.json` sidecar inside a zip.

- Missing zip parts `002`, `004`, … are often those videos, not failed downloads.
- `IMG_3173-040.MOV` means zip part **040**; mosaicWave treats it as `IMG_3173.MOV` next to that sidecar.

Check folder warns only if a zip part is missing **and** there is no matching `*-NNN` file (`missing_zip_part` — re-download), or a sidecar has no bytes in any zip or loose file.

## Check folder / Import

`POST /api/v1/import/takeout/inspect` then `POST /api/v1/import/takeout` with the **download folder** path. Poll `GET /api/v1/import/takeout/status` until `done` (the UI does this). Import is not one long HTTP request — hashing tens of GB through the Next.js `/api` rewrite resets the socket.

Import is blocked only when the zips contain no photos (and there is no extracted tree either). Re-import of a partial run is safe: already-hashed files (same path, size, mtime) are not hashed again.

Do not commit personal dumps (`google photos/` is gitignored). Keep the API process running until status is `done` (do not save server Python during import if uvicorn `--reload` is on). In `next dev`, file uploads skip the `/api` rewrite (Next’s default 10MB body cap; a 4GB cap is still too small — leftover videos exceed the zip split) and POST to uvicorn on `:8000`. Push uses a raw body, not multipart (Starlette’s default part cap is 1MB). The push size cap is 256GB.
