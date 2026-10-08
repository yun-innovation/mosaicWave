# FileStore

**v1 status: done for local folders/shares.** Phase 1 protocol + `LocalFileStore` / `MemoryFileStore`; Phase 2 scan and Takeout use it; Phase 4 Range + thumbs spool; Phase 5 upload. **Do not start Google Drive / OneDrive / iCloud backends in Phase 7.** Later work: [filestore-later.md](filestore-later.md).

Thumbs and other derived bytes are **BlobStore**, not FileStore — [storage.md](storage.md). SQLite holds pointers only — [schema.md](schema.md).

Code: `src/server/mosaicwave/storage/filestore.py` (`FileStore` protocol, `LocalFileStore`, `MemoryFileStore`). Takeout downloads: `takeoutzip.py` (`TakeoutZipStore` reads `takeout-*.zip` plus loose oversized files). Path rules: `paths.py`. Errors: `errors.py`. Host `smb://` / UNC in Settings and Browse: [smb-paths.md](smb-paths.md).

## Role

Scan, Takeout, media download/upload, and (later) original HTTP Range must not call `open()` / `os.path` on library files except **inside** a FileStore backend. QNAP shared folders are ordinary directories on the NAS; Windows and Linux folders use the same `LocalFileStore`.

| | FileStore | BlobStore | SQLite |
| --- | --- | --- | --- |
| Holds | Original JPEG/MP4 in **user folders/shares** | Derived thumbs / previews | Metadata |
| Address | `source` + `relative_path` (on-disk tree) | Opaque key (`thumb/{asset_id}/…`) | `asset.id` (UUID) |
| v1 backend | Directory at `source.root_uri` (Windows MSI: `%PROGRAMDATA%\mosaicWave\libraries\{user_id}`; debug: `...\mosaicWave-dev\...`) | `{data_dir}/blobs/` | `{bootstrap}/library.db` |

**Originals stay where the user puts them.** mosaicWave indexes that tree; it does **not** copy the live library into the QPKG/data dir. Qfile, File Station, SMB, and mosaicWave upload all write the **same** FileStore. Scan picks up adds/renames/deletes.

**BlobStore** is the efficient derived store: mosaicWave-owned, hashed keys, not a second original.

**Takeout** is a one-shot dump. Import **copies unique files into a folder source** (a share the user already uses). Then the dump may be deleted. Live paths are ordinary filenames in that folder, not Google album trees and not `YYYY/MM/hash`.

## Virtual paths

`asset.relative_path` is a virtual path **inside** one source root:

- Separators are `/` only (Windows `\` is normalized on input)
- No drive letters, no leading `/` required (root is `""` or `"."`)
- No `.` or `..` segments
- Not a primary key (Takeout ingest copies into a folder source using a basename that SMB/Windows can store; `:` and other illegal characters become `_`)

Examples under a source rooted at `D:\Pictures`:

| Host path | `relative_path` |
| --- | --- |
| `D:\Pictures\IMG_0001.jpg` | `IMG_0001.jpg` |
| `D:\Pictures\2024\trip\a.jpg` | `2024/trip/a.jpg` |

`source.root_uri` is the host locator (`D:\Pictures`, `/share/Photos`, …). Clients never receive it. Folder UI, if added, splits `relative_path` — there is no `folder` table.

## Protocol

```
stat(rel) -> FileStat(size, mtime, is_dir)
list(rel) -> names in that directory only (sorted, not recursive)
walk()    -> every file under the root (recursive)
exists(rel)
delete(rel)
open_read(rel)  -> binary stream
open_write(rel) -> create/replace; local backend is atomic (temp + replace)
```

`rel` is always virtual. `resolve` stays inside the backend; FastAPI handlers must not put host paths in JSON.

### `walk` vs `list`

| | `walk()` | `list(rel)` |
| --- | --- | --- |
| Recurses | Yes | No |
| Yields | File virtual paths | Immediate child names (files and dirs) |
| Used by | Folder scan, Takeout import | Future folder browser |

Indexer skips non-media in Python (`is_media` in `library/media.py`): images `.jpg` `.jpeg` `.png` `.gif` `.webp` `.heic` `.heif` `.tif` `.tiff` `.bmp` `.dng`; video `.mp4` `.mov` `.m4v` `.avi` `.mkv` `.webm` `.3gp`. Sidecar `*.json` is not an asset; Takeout reads it next to the media file.

## LocalFileStore

Rooted at a filesystem path. Constructor `mkdir`s the root if missing, then `resolve()`s it.

**Jail:** join + `resolve` must stay under that root. `..`, extra slashes, and junctions/symlinks that escape raise `InvalidPath`. **Symlinks are not followed** (`lstat`; `walk` skips symlink files). Windows reserved device names (`CON`, `NUL`, `COM1`, …) are skipped on walk/list and rejected on open/stat.

**Atomic write:** `open_write` writes `mw-*.tmp` in the destination directory, `fsync`s, then `os.replace`. A crash must not leave a truncated original. The temp name must **not** start with `.` — QNAP SMB shares reserve a leading dot (`.@recycle`, `.@__thumb`).

**`delete`:** files via `unlink`; directories via `rmdir` (empty only).

QNAP `/share/Photos`, `D:\Pictures`, and `/home/me/photos` are the same class. Object storage is a later backend; it must not require changing `asset.id` or the HTTP API.

## MemoryFileStore

In-memory dict of virtual path → bytes. Pytest only. No real photo tree required.

## Mapping to SQLite

| DB | FileStore |
| --- | --- |
| `source.backend` | `local` in v1 |
| `source.root_uri` | `LocalFileStore` root |
| `source.kind` | How the walker interprets the tree (`folder` vs `takeout`) |
| `asset.relative_path` | Virtual path from `walk()` |
| `asset.size` / `mtime` | `stat(rel)` |
| `asset.content_hash` | SHA-256 of `open_read` bytes |

Same `root_uri` + `kind` reuses the `source` row. Scan/import is synchronous HTTP today: a huge tree hashes and EXIF-reads every media file in one request and can time out. SQLite itself is not the bottleneck.

## Many files in one folder

Allowed. FileStore is hierarchical, not flat-only: a directory of 10 000 JPEGs is 10 000 `walk()` yields (`IMG_0001.jpg`, …).

- **Folder scan:** one `asset` per path. Same bytes under two names → two rows. Parent folder name is **not** an album. Qfile/SMB writes show up on the next scan.
- **Takeout ingest:** read the dump, SHA-256 dedup, **copy** unique files into a **folder source** using `safe_library_filename` (`unused_relative` on collision). Album names from dump folders go to SQLite only.

`GET /api/v1/assets` pages by `taken_at` (default 50, max 200), not by folder. The web timeline loads pages with **Load more**.

## Errors

Callers must not swallow a bare `Exception` to hide jail failures.

| Class | Typical cause |
| --- | --- |
| `NotFound` | Missing file |
| `PermissionDenied` | OS ACL, or non-empty dir delete in memory store |
| `InvalidPath` | `..`, reserved name, symlink, not a file/dir as required |
| `NotSupported` | Reserved for later backends |

Scan records a fatal walk error on `source.error`. Per-file OS errors on `walk` skip that file.

## What FileStore does not do

- Thumbnail generation (BlobStore; Phase 4 `GET /assets/{id}/thumb`)
- Hash or EXIF (library layer, using `open_read`)
- Watching the disk (`inotify` / `ReadDirectoryChanges` later; same scan API)
- Serving host paths to the browser
- FUSE / virtual drives
- Hash-directory layout of originals (`YYYY/MM/ab/cd/…`) — that is BlobStore, not the live share
- Cloud drive backends in v1 — [filestore-later.md](filestore-later.md)
