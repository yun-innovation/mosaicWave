"""Resumable single-file upload for the mobile apps (doc/mobile.md § Upload design).

An upload is two files in ``{data_dir}/tmp/uploads/{user_id}/``: ``{id}.part`` (the bytes so far; its size
*is* the offset) and ``{id}.json`` (what the client declared). No database table. The last byte finishes the
upload: the whole file's SHA-256 must equal the declared hash, then the file moves into the user's library
under ``Uploads/{year}/`` and is indexed like any other file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mosaicwave.config import Settings
from mosaicwave.library.media import guess_mime, is_media, taken_at_from_exif, taken_at_from_video
from mosaicwave.library.takeout import datetime_from_mtime, index_media_file
from mosaicwave.models import Asset, Source, iso_utc, new_id
from mosaicwave.storage.filestore import LocalFileStore
from mosaicwave.storage.paths import safe_library_filename

MAX_FILE_BYTES = 64 * 1024**3  # one photo or video
MAX_OPEN_PER_USER = 100
CHUNK_SIZE = 8 * 1024 * 1024  # what the server suggests; any size works
STALE_AFTER = timedelta(days=7)  # an upload nobody touched this long is deleted
CHECK_MAX = 500
FOLDER = "Uploads"

_HASH = re.compile(r"^[0-9a-f]{64}$")


class UploadError(Exception):
    """A refusal with the status and stable code the API answers (translated as ``errors:{code}``)."""

    def __init__(self, status: int, code: str, message: str, params: dict | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.params = params or {}


@dataclass
class UploadMeta:
    id: str
    filename: str
    size: int
    content_hash: str
    taken_at: str | None  # ISO 8601 UTC the app declared (used only when the file itself has no date)
    mime: str
    created_at: str
    touched_at: str

    @property
    def expires_at(self) -> str:
        return iso_utc(_parse(self.touched_at) + STALE_AFTER) or ""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def uploads_root(settings: Settings) -> Path:
    return settings.data_dir / "tmp" / "uploads"


def user_dir(settings: Settings, user_id: str) -> Path:
    return uploads_root(settings) / user_id


def _part(folder: Path, upload_id: str) -> Path:
    return folder / f"{upload_id}.part"


def _meta(folder: Path, upload_id: str) -> Path:
    return folder / f"{upload_id}.json"


_ID = re.compile(r"^[0-9a-f-]{36}$")


def normalize_hash(raw: str) -> str:
    value = (raw or "").strip().lower()
    if not _HASH.match(value):
        raise UploadError(400, "invalid_hash", "content_hash must be a SHA-256 in hex (64 characters)")
    return value


def _save(folder: Path, meta: UploadMeta) -> None:
    tmp = _meta(folder, meta.id).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(meta)), encoding="utf-8")
    tmp.replace(_meta(folder, meta.id))


def load(folder: Path, upload_id: str) -> UploadMeta:
    if not _ID.match(upload_id):
        raise UploadError(404, "upload_not_found", "no such upload")
    try:
        data = json.loads(_meta(folder, upload_id).read_text(encoding="utf-8"))
        return UploadMeta(**data)
    except (OSError, ValueError, TypeError) as exc:
        raise UploadError(404, "upload_not_found", "no such upload") from exc


def offset_of(folder: Path, upload_id: str) -> int:
    try:
        return _part(folder, upload_id).stat().st_size
    except FileNotFoundError:
        return 0


def touch(folder: Path, meta: UploadMeta) -> None:
    meta.touched_at = iso_utc(_now()) or ""
    _save(folder, meta)


def list_open(folder: Path) -> list[UploadMeta]:
    found: list[UploadMeta] = []
    if folder.is_dir():
        for path in folder.glob("*.json"):
            try:
                found.append(UploadMeta(**json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, ValueError, TypeError):
                continue
    return found


def discard(folder: Path, upload_id: str) -> None:
    for path in (_part(folder, upload_id), _meta(folder, upload_id)):
        path.unlink(missing_ok=True)
    for path in folder.glob(f"{upload_id}.*"):  # a half-finished move or rename
        path.unlink(missing_ok=True)


def remove_stale(folder: Path, *, now: datetime | None = None) -> int:
    """Delete uploads nobody touched for ``STALE_AFTER`` (and part files with no description)."""
    if not folder.is_dir():
        return 0
    cutoff = (now or _now()) - STALE_AFTER
    gone = 0
    for meta in list_open(folder):
        if _parse(meta.touched_at) < cutoff:
            discard(folder, meta.id)
            gone += 1
    for part in folder.glob("*.part"):
        if not _meta(folder, part.stem).exists() and datetime.fromtimestamp(part.stat().st_mtime, timezone.utc) < cutoff:
            part.unlink(missing_ok=True)
    return gone


def remove_stale_all(settings: Settings) -> None:
    """Start-up: clean every user's staging folder."""
    root = uploads_root(settings)
    if root.is_dir():
        for folder in root.iterdir():
            if folder.is_dir():
                remove_stale(folder)


def create(
    settings: Settings,
    user_id: str,
    *,
    filename: str,
    size: int,
    content_hash: str,
    taken_at: datetime | None,
    mime: str | None,
) -> tuple[UploadMeta, bool]:
    """Start an upload, or return the open one for the same hash. The bool is True when it is new."""
    digest = normalize_hash(content_hash)
    folder = user_dir(settings, user_id)
    remove_stale(folder)
    for meta in list_open(folder):
        if meta.content_hash == digest:
            touch(folder, meta)
            return meta, False
    name = safe_library_filename(filename)
    if not is_media(name):
        raise UploadError(415, "unsupported_media_type", "only photos and videos can be uploaded")
    if size < 1:
        raise UploadError(400, "upload_empty", "an empty file cannot be uploaded")
    if size > MAX_FILE_BYTES:
        raise UploadError(413, "upload_too_large", "file too large", {"max_gb": MAX_FILE_BYTES >> 30})
    if len(list_open(folder)) >= MAX_OPEN_PER_USER:
        raise UploadError(429, "upload_limit", "too many unfinished uploads", {"max": MAX_OPEN_PER_USER})
    folder.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(folder).free < size:
        raise UploadError(507, "insufficient_storage", "not enough free space on the server")
    now = iso_utc(_now()) or ""
    meta = UploadMeta(
        id=new_id(),
        filename=name,
        size=size,
        content_hash=digest,
        taken_at=iso_utc(taken_at) if taken_at else None,
        mime=(mime or guess_mime(name))[:127],
        created_at=now,
        touched_at=now,
    )
    _part(folder, meta.id).touch()
    _save(folder, meta)
    return meta, True


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


@contextmanager
def busy(upload_id: str) -> Iterator[None]:
    """One writer per upload; a second one is refused instead of interleaving bytes."""
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(upload_id, threading.Lock())
    if not lock.acquire(blocking=False):
        raise UploadError(409, "upload_busy", "this upload is being written by another request")
    try:
        yield
    finally:
        lock.release()
        with _LOCKS_GUARD:
            _LOCKS.pop(upload_id, None)


async def append(folder: Path, meta: UploadMeta, start: int, chunks: AsyncIterator[bytes]) -> int:
    """Append the request body at ``start``. Returns the bytes written.

    A body that would pass the declared size is refused and the file is cut back to ``start``. A client that
    drops mid-body leaves what arrived: ``HEAD`` then reports the true offset.
    """
    limit = meta.size - start
    written = 0
    path = _part(folder, meta.id)
    with path.open("r+b") as out:
        out.truncate(start)
        out.seek(start)
        try:
            async for chunk in chunks:
                if not chunk:
                    continue
                written += len(chunk)
                if written > limit:
                    out.truncate(start)
                    raise UploadError(400, "upload_too_long", "more bytes than the declared size", {"size": meta.size})
                out.write(chunk)
        finally:
            out.flush()
    return written


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def known_hashes(session: Session, hashes: list[str]) -> tuple[set[str], set[str]]:
    """Of ``hashes``: those a live photo has, and those only a deleted photo has (the user removed them)."""
    known: set[str] = set()
    removed: set[str] = set()
    for at in range(0, len(hashes), CHECK_MAX):
        chunk = hashes[at : at + CHECK_MAX]
        rows = session.execute(
            select(Asset.content_hash, Asset.deleted_at).where(Asset.content_hash.in_(chunk))
        ).all()
        for digest, deleted_at in rows:
            (removed if deleted_at is not None else known).add(digest)
    return known, removed - known


def live_asset_with_hash(session: Session, digest: str) -> Asset | None:
    return session.scalar(
        select(Asset).where(Asset.content_hash == digest, Asset.deleted_at.is_(None)).order_by(Asset.id).limit(1)
    )


def _free_name(store: LocalFileStore, folder: str, name: str) -> str:
    stem, suffix = Path(name).stem, Path(name).suffix
    candidate, n = name, 0
    while store.exists(f"{folder}/{candidate}"):
        n += 1
        candidate = f"{stem}-{n}{suffix}"
    return f"{folder}/{candidate}"


def finish(session: Session, source: Source, folder: Path, meta: UploadMeta) -> tuple[str, Asset]:
    """The last byte arrived. Verify, then file it. Returns ``("done" | "exists", asset)``."""
    part = _part(folder, meta.id)
    if part.stat().st_size != meta.size:
        raise UploadError(400, "upload_incomplete", "the file is not complete")
    if _sha256(part) != meta.content_hash:
        discard(folder, meta.id)
        raise UploadError(422, "upload_hash_mismatch", "the bytes do not match content_hash; start this upload again")

    existing = live_asset_with_hash(session, meta.content_hash)
    if existing is not None:
        discard(folder, meta.id)
        return "exists", existing

    staged_rel = f"{meta.id}{Path(meta.filename).suffix.lower()}"
    staged = folder / staged_rel
    part.replace(staged)
    stage_store = LocalFileStore(folder)
    taken = taken_at_from_exif(stage_store, staged_rel) or taken_at_from_video(stage_store, staged_rel)
    if taken is None and meta.taken_at:
        taken = _parse(meta.taken_at)
    if taken is None:
        taken = datetime_from_mtime(staged.stat().st_mtime)
    if taken.tzinfo is None:
        taken = taken.replace(tzinfo=timezone.utc)
    taken = taken.astimezone(timezone.utc)

    library = LocalFileStore(source.root_uri)
    rel = _free_name(library, f"{FOLDER}/{taken.year:04d}", meta.filename)
    dest = Path(source.root_uri) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(os.fspath(staged), os.fspath(dest))
    stamp = taken.timestamp()
    os.utime(dest, (stamp, stamp))  # the file's own date agrees with the capture time

    asset = index_media_file(session, source, library, rel)
    _meta(folder, meta.id).unlink(missing_ok=True)
    return "done", asset


__all__ = [
    "CHUNK_SIZE",
    "UploadError",
    "UploadMeta",
    "append",
    "busy",
    "create",
    "discard",
    "finish",
    "known_hashes",
    "live_asset_with_hash",
    "load",
    "offset_of",
    "remove_stale",
    "remove_stale_all",
    "touch",
    "user_dir",
]
