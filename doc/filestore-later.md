# FileStore later (bring-your-own storage)

**Not a phase.** Do not implement from this doc until the item is promoted on [roadmap.md](roadmap.md). v1 local FileStore is **done** — [filestore.md](filestore.md). Brainstorm item: user’s own cloud drive.

## Background (read this first)

[storage.md](storage.md) numbers requirements **S1–S15**. Those are checklist IDs, **not** Amazon S3.

| Term | Meaning here |
| --- | --- |
| **S1–S14** | v1 storage rules (protocol, local disk, jail, thumbs in BlobStore, …). Already implemented. |
| **S3** (the requirement) | “Ship `LocalFileStore` / `LocalBlobStore`.” A folder on the NAS or PC. |
| **S15** | Later: add **another** FileStore backend (cloud drive or object storage) **without** changing photo UUIDs or `/api/v1`. Not built yet. |
| **Amazon S3** | AWS **Simple Storage Service**. You put a **file** at a **key** (a string like `photos/a.jpg`) inside a **bucket** (a named container). There are no real folders; `/` in the key is only a naming convention. MinIO, Wasabi, Cloudflare R2, Backblaze B2, and many NAS “S3” apps speak a similar HTTP API — **S3-compatible**. |
| **Object storage** | The family Amazon S3 belongs to: address bytes by key, HTTP GET/PUT, optional Range. Contrast with a **filesystem** (`D:\Pictures\a.jpg`, QNAP `/share/Photos`). |
| **FileStore** | mosaicWave’s Python interface for **originals**: virtual paths, list/walk, read/write. Today one implementation: local disk. S15 says a second implementation (Drive, OneDrive, S3-compatible, …) should plug in behind the same interface. |
| **BlobStore** | Separate store for **derived** bytes (thumbs). Keys like `thumb/{uuid}/1.jpg`. Not the original JPEG. Already local under `{data_dir}/blobs/`. |
| **Backend / factory** | `source.backend` is a string (`local` today). A **factory** looks at that string and returns the right FileStore. Today it always opens a local folder. |
| **OAuth** | “Sign in with Google/Microsoft.” The user grants mosaicWave a **token** to call Drive/OneDrive. Tokens are secrets; they must not be stored in `root_uri` (that column is a path/id, and clients must never see it). |
| **ETag** | A short version stamp the provider returns (“this file is still the same”). Scan can skip re-download if the etag did not change. Cheaper than hashing every byte over the network. |
| **Delta / change feed** | “What changed since last time?” Drive and OneDrive can list changes. Full `walk()` of a cloud account is slow and hits rate limits. |
| **BYO** | Bring your own: the user’s Drive/OneDrive/S3, not mosaicWave hosting photos in *our* cloud. |

**S15 in one sentence:** keep the gallery and SQLite the same; only the class that reads/writes original files changes when the photos live in the user’s cloud instead of `/share/Photos`.

## Do we need more FileStore coding now?

**No, not scheduled.** Local originals I/O is enough for QNAP shares, Windows/Linux folders, Takeout ingest into a folder source, upload, Range, and thumbs.

| Piece | Status |
| --- | --- |
| `FileStore` protocol (`stat` / `list` / `walk` / `exists` / `delete` / `open_read` / `open_write`) | Done |
| `LocalFileStore` (jail, atomic write, no symlink follow) | Done |
| `MemoryFileStore` (pytest) | Done |
| `TakeoutZipStore` (read-only dump) | Done — dump only, not a live source |
| Folder scan + upload + `GET .../file` via FileStore | Done |
| BlobStore thumbs (`{data_dir}/blobs/`, opaque keys) | Done |
| Factory `source.backend` → store | **Not done** — always `LocalFileStore(Path(root_uri))` |
| Google Drive / OneDrive / iCloud Drive / S3 | **Not done** — parking lot |
| Delta/change scan (vs full `walk()`) | **Not done** |
| OAuth / per-source credentials | **Not done** |

Code more FileStore only when: a **local** bug (jail, Range, scan miss), or a **promoted** backend. Do not add Drive “while we are here.”

## Layout (locked for originals)

QuMagie + Qfile pattern for **live originals**: the user’s folder/share **is** the library. Qfile, File Station, SMB, Explorer, and mosaicWave upload write ordinary names. Identity is `(source_id, relative_path)`. Scan sees external adds/deletes.

**Do not** store live originals as `YYYY/MM/{hash}/file.jpg` or under `{data_dir}/files/`. Hash namespaces belong in **BlobStore** keys (`thumb/{asset_id}/{rev}.jpg`). A later object-storage FileStore may use opaque keys **inside that backend** because there is no File Station; still do not rewrite a Drive user’s `Camera/IMG_1234.jpg` into a hash tree.

Takeout is a **one-shot ingest** into a folder source (SMB-safe filename, rename on collision). The dump may be deleted. [takeout.md](takeout.md).

## What a later backend must keep

- Same `asset.id` (UUID) and HTTP API. Clients never see `root_uri`.
- Virtual `relative_path` with `/` only ([filestore.md](filestore.md)).
- Callers talk only to the protocol — no `pathlib` on library bytes except inside a backend.
- `NotSupported` when the provider cannot rename, watch, or Range-seek.
- File Station coexistence is **local-disk only** ([storage.md](storage.md) P13). Cloud sources do not promise a side-channel tree on the NAS.

## Gaps to close when promoting a backend

1. **Factory** — `filestore_for_source` must branch on `source.backend`, not always `LocalFileStore`. Today `scan_folder` / Takeout dest still take a host `Path`.
2. **Locator vs secrets** — `root_uri` is a filesystem path for `local`. Drive needs a folder id. OAuth tokens must **not** live in `root_uri`; add a credentials table or secret store later.
3. **Scan** — `walk()` lists the whole tree. Fine on a share; on Drive it rate-limits. Need list pagination and a **delta/change** cursor. Full SHA-256 of every file over WAN is too expensive; prefer provider etag / md5 when honest.
4. **Writes** — local `open_write` is temp + `os.replace`. Cloud is a resumable upload. Keep the protocol “write a stream”; keep atomic rename inside `LocalFileStore`.
5. **`FileStat`** — today size / mtime / is_dir. Cloud needs etag (and optional provider hash) so scan can skip download.
6. **Admin picker** — `/api/v1/fs/list` is host directories. A Drive source needs a Drive folder picker.
7. **Protocol typing** — `open_read` is used as a context manager; the Protocol currently says `Iterator`. Fix when touching the protocol.
8. **Range** — `serve.py` already skips bytes if the stream is not seekable. Prefer ranged GET on the provider when available.
9. **Thumbs** — already spool to temp when `handle.name` is not a local file. Cloud originals will use that path.

## Bring-your-own providers (shape)

| User says | FileStore-shaped? | Notes |
| --- | --- | --- |
| **Google Drive** / **OneDrive** / **Dropbox** / **iCloud Drive** (Files) | Yes | Folder tree; OAuth; map to `backend` + folder id |
| **S3-compatible** | Yes | Often key/prefix, not user folders; still one backend |
| **iCloud Photos** | **No** | Photo library API, not a directory. Closer to Takeout / Google Photos than FileStore. Do not pretend `backend=icloud` covers both Drive and Photos |
| **Google Photos** | **No** | Out of scope (Takeout ingest only) |

mosaicWave stays on the **user’s host** (QNAP or standalone) and talks to **their** drive. That is not mosaicWave-as-SaaS and not storing the library in *our* cloud ([roadmap.md](roadmap.md) Brainstorm).

## Suggested implementation order (when promoted)

1. Factory + keep `local` behaving as today.
2. One cloud FileStore (Drive or OneDrive) with OAuth, list+delta, download stream, upload session.
3. Scan uses etag; thumbs still BlobStore on the host.
4. UI: add source of type Drive (not the host folder picker).
5. Only then consider S3 or iCloud Drive. iCloud Photos stays a separate ingest discussion.

Pytest: fake FileStore with etag + paged list; no live cloud account required (same rule as [storage.md](storage.md) S12).
