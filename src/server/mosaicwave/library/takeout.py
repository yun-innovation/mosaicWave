from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from mosaicwave.library.dump import find_photos_root
from mosaicwave.library.media import (
    extra_from_sidecar,
    filename,
    guess_mime,
    is_album_folder,
    is_media,
    parent_name,
    read_sidecar,
    sha256_file,
    taken_at_from_exif,
    taken_at_from_sidecar,
    taken_at_from_video,
)
from mosaicwave.library.sync import assign_sync_rev
from mosaicwave.models import Album, AlbumAsset, Asset, Source, new_id, utcnow
from mosaicwave.storage.filestore import FileStore, LocalFileStore
from mosaicwave.storage.errors import NotFound as StoreNotFound
from mosaicwave.storage.paths import safe_library_filename
from mosaicwave.storage.takeoutzip import TakeoutZipStore, has_takeout_zips


def _touch(row: Asset | Album | AlbumAsset | Source) -> None:
    session = object_session(row)
    if session is not None and isinstance(row, (Asset, Album, AlbumAsset)):
        assign_sync_rev(session, row)
        return
    row.rev = int(row.rev) + 1
    row.updated_at = utcnow()


def _ensure_source(session: Session, root: Path, kind: str, *, owner_user_id: str | None = None) -> Source:
    uri = str(root.resolve())
    source = session.scalar(
        select(Source).where(Source.root_uri == uri, Source.kind == kind, Source.deleted_at.is_(None))
    )
    if source:
        if owner_user_id and source.owner_user_id not in (None, owner_user_id):
            raise PermissionError("that folder is another user's library")
        if owner_user_id and source.owner_user_id is None:
            source.owner_user_id = owner_user_id
        return source
    source = Source(
        id=new_id(),
        backend="local",
        root_uri=uri,
        kind=kind,
        owner_user_id=owner_user_id,
    )
    session.add(source)
    session.flush()
    return source


def _ensure_album(session: Session, name: str, source_id: str) -> Album:
    album = session.scalar(
        select(Album).where(
            Album.name == name,
            Album.source_id == source_id,
            Album.deleted_at.is_(None),
        )
    )
    if album:
        return album
    album = Album(id=new_id(), name=name, source_id=source_id)
    assign_sync_rev(session, album)
    session.add(album)
    session.flush()
    return album


def _ensure_membership(session: Session, album: Album, asset: Asset) -> None:
    existing = session.scalar(
        select(AlbumAsset).where(AlbumAsset.album_id == album.id, AlbumAsset.asset_id == asset.id)
    )
    if existing:
        if existing.deleted_at is not None:
            existing.deleted_at = None
            _touch(existing)
        return
    link = AlbumAsset(id=new_id(), album_id=album.id, asset_id=asset.id)
    assign_sync_rev(session, link)
    session.add(link)
    if album.cover_asset_id is None:
        album.cover_asset_id = asset.id
        _touch(album)


def _apply_asset_fields(
    asset: Asset,
    *,
    rel: str,
    size: int,
    mtime: float,
    mime: str,
    taken_at,
    extra: dict | None,
    prefer_path: bool,
) -> bool:
    changed = False
    if prefer_path and asset.relative_path != rel:
        asset.relative_path = rel
        changed = True
    if asset.size != size:
        asset.size = size
        changed = True
    if asset.mtime != mtime:
        asset.mtime = mtime
        changed = True
    if asset.mime != mime:
        asset.mime = mime
        changed = True
    if taken_at is not None and asset.taken_at != taken_at:
        asset.taken_at = taken_at
        changed = True
    extra_text = json.dumps(extra, sort_keys=True) if extra else None
    if extra_text and extra_text != asset.extra_json:
        asset.extra_json = extra_text
        changed = True
    if asset.deleted_at is not None:
        asset.deleted_at = None
        changed = True
    if changed:
        _touch(asset)
    return changed


def index_media_file(session: Session, source: Source, store: FileStore, rel: str) -> Asset:
    st = store.stat(rel)
    sidecar = read_sidecar(store, rel)
    taken = taken_at_from_sidecar(sidecar) if sidecar else None
    if taken is None:
        taken = taken_at_from_exif(store, rel)
    if taken is None:
        taken = taken_at_from_video(store, rel)
    if taken is None:
        taken = datetime_from_mtime(st.mtime)
    extra = extra_from_sidecar(sidecar) if sidecar else None
    asset = session.scalar(
        select(Asset).where(Asset.source_id == source.id, Asset.relative_path == rel)
    )
    if asset is None:
        asset = Asset(
            id=new_id(),
            source_id=source.id,
            relative_path=rel,
            size=st.size,
            mtime=st.mtime,
            mime=guess_mime(rel),
            taken_at=taken,
            extra_json=json.dumps(extra, sort_keys=True) if extra else None,
            content_hash=sha256_file(store, rel),
        )
        assign_sync_rev(session, asset)
        session.add(asset)
        session.flush()
        return asset
    _apply_asset_fields(
        asset,
        rel=rel,
        size=st.size,
        mtime=st.mtime,
        mime=guess_mime(rel),
        taken_at=taken,
        extra=extra,
        prefer_path=False,
    )
    session.flush()
    return asset


def unused_relative(store: FileStore, name: str) -> str:
    name = safe_library_filename(name)
    stem = Path(name).stem
    suffix = Path(name).suffix
    candidate = name
    n = 0
    while True:
        try:
            store.stat(candidate)
        except StoreNotFound:
            return candidate
        n += 1
        candidate = f"{stem}-{n}{suffix}"


def scan_folder(session: Session, root: Path, *, owner_user_id: str | None = None) -> Source:
    root = root.resolve()
    store: FileStore = LocalFileStore(root)
    source = _ensure_source(session, root, "folder", owner_user_id=owner_user_id)
    seen_paths: set[str] = set()
    try:
        for rel in store.walk():
            if not is_media(rel):
                continue
            seen_paths.add(rel)
            index_media_file(session, source, store, rel)
        _tombstone_missing_paths(session, source, seen_paths)
        source.last_scan_at = utcnow()
        source.error = None
        _touch(source)
    except Exception as exc:
        source.error = str(exc)
        _touch(source)
        raise
    return source


def datetime_from_mtime(mtime: float):
    from datetime import datetime, timezone

    return datetime.fromtimestamp(mtime, tz=timezone.utc)


def _tombstone_missing_paths(session: Session, source: Source, seen: set[str]) -> None:
    now = utcnow()
    rows = session.scalars(
        select(Asset).where(Asset.source_id == source.id, Asset.deleted_at.is_(None))
    ).all()
    for asset in rows:
        if asset.relative_path not in seen:
            asset.deleted_at = now
            _touch(asset)


def filestore_for_source(source: Source) -> FileStore:
    root = Path(source.root_uri)
    if source.kind == "takeout":
        _base, store = takeout_store_for(root)
        return store
    return LocalFileStore(root)


def takeout_store_for(root: Path) -> tuple[Path, FileStore]:
    """Selected dump folder: zips → TakeoutZipStore; otherwise LocalFileStore on an extracted tree."""
    root = root.resolve()
    if has_takeout_zips(root):
        return root, TakeoutZipStore(root)
    photos = find_photos_root(root)
    base = photos or root
    return base, LocalFileStore(base)


def copy_into_store(src: FileStore, src_rel: str, dest: FileStore, dest_rel: str) -> None:
    if dest.exists(dest_rel):
        return
    with src.open_read(src_rel) as inp, dest.open_write(dest_rel) as out:
        while True:
            chunk = inp.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)


def import_takeout(
    session: Session,
    root: Path,
    *,
    dest_root: Path,
    owner_user_id: str | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
    commit_every: int = 0,
) -> Source:
    root = root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Takeout path is not a directory: {root}")
    dest_root = dest_root.expanduser().resolve()
    dest = LocalFileStore(dest_root)
    source = _ensure_source(session, dest_root, "folder", owner_user_id=owner_user_id)
    _dump_root, store = takeout_store_for(root)
    media_rels = [rel for rel in store.walk() if is_media(rel)]
    total = len(media_rels)
    try:
        for index, rel in enumerate(media_rels, start=1):
            if on_progress:
                on_progress(index - 1, total, rel)
            digest = sha256_file(store, rel)
            sidecar = read_sidecar(store, rel)
            taken = taken_at_from_sidecar(sidecar) if sidecar else None
            if taken is None:
                taken = taken_at_from_exif(store, rel)
            if taken is None:
                taken = taken_at_from_video(store, rel)
            if taken is None:
                taken = datetime_from_mtime(store.stat(rel).mtime)
            extra = extra_from_sidecar(sidecar) if sidecar else None
            folder = parent_name(rel)
            album_folder = is_album_folder(folder)

            asset = session.scalar(
                select(Asset).where(Asset.source_id == source.id, Asset.content_hash == digest)
            )
            dest_rel = unused_relative(dest, filename(rel))
            if asset is None:
                try:
                    copy_into_store(store, rel, dest, dest_rel)
                except OSError as exc:
                    raise OSError(f"{rel}: {exc}") from exc
                stored = dest.stat(dest_rel)
                asset = Asset(
                    id=new_id(),
                    source_id=source.id,
                    relative_path=dest_rel,
                    size=stored.size,
                    mtime=stored.mtime,
                    mime=guess_mime(rel),
                    taken_at=taken,
                    extra_json=json.dumps(extra, sort_keys=True) if extra else None,
                    content_hash=digest,
                )
                assign_sync_rev(session, asset)
                session.add(asset)
                session.flush()
            else:
                if not dest.exists(asset.relative_path):
                    try:
                        copy_into_store(store, rel, dest, asset.relative_path)
                    except OSError as exc:
                        raise OSError(f"{rel}: {exc}") from exc
                stored = dest.stat(asset.relative_path)
                _apply_asset_fields(
                    asset,
                    rel=asset.relative_path,
                    size=stored.size,
                    mtime=stored.mtime,
                    mime=guess_mime(asset.relative_path),
                    taken_at=taken,
                    extra=extra,
                    prefer_path=False,
                )

            if album_folder:
                album = _ensure_album(session, folder, source.id)
                _ensure_membership(session, album, asset)

            if on_progress:
                on_progress(index, total, rel)
            if commit_every > 0 and index % commit_every == 0:
                session.commit()

        source.last_scan_at = utcnow()
        source.error = None
        _touch(source)
    except Exception as exc:
        source.error = str(exc)
        _touch(source)
        raise
    return source
