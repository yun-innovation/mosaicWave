"""Manual (virtual) albums: SQLite membership only; originals stay in FileStore."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mosaicwave.library.sync import assign_sync_rev
from mosaicwave.library.takeout import _ensure_membership, _touch
from mosaicwave.models import Album, AlbumAsset, Asset, new_id, utcnow


class AlbumConflict(Exception):
    """Live album name already used in this library."""


class AlbumStale(Exception):
    """Replica push used a stale base_rev."""

    def __init__(self, server_rev: int):
        self.server_rev = int(server_rev)
        super().__init__(f"album changed on the server (rev {self.server_rev})")


def check_base_rev(album: Album, base_rev: int | None) -> None:
    if base_rev is None:
        return
    if int(album.rev) != int(base_rev):
        raise AlbumStale(int(album.rev))


def _live_count(session: Session, album_id: str) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(AlbumAsset)
            .where(
                AlbumAsset.album_id == album_id,
                AlbumAsset.deleted_at.is_(None),
            )
        )
        or 0
    )


def album_view(session: Session, row: Album) -> dict:
    return {
        "id": row.id,
        "source_id": row.source_id,
        "name": row.name,
        "cover_asset_id": row.cover_asset_id,
        "sort": int(row.sort or 0),
        "rev": int(row.rev or 0),
        "asset_count": _live_count(session, row.id),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_albums(session: Session, *, source_ids: set[str]) -> list[dict]:
    if not source_ids:
        return []
    rows = list(
        session.scalars(
            select(Album)
            .where(Album.source_id.in_(source_ids), Album.deleted_at.is_(None))
            .order_by(Album.sort.asc(), Album.name.asc(), Album.id.asc())
        )
    )
    return [album_view(session, row) for row in rows]


def get_album(session: Session, *, source_id: str, album_id: str) -> Album:
    return get_visible_album(session, album_id=album_id, source_ids={source_id})


def get_visible_album(session: Session, *, album_id: str, source_ids: set[str]) -> Album:
    row = session.get(Album, album_id)
    if row is None or row.deleted_at is not None or row.source_id not in source_ids:
        raise FileNotFoundError("album not found")
    return row


def _clean_name(name: str) -> str:
    text = (name or "").strip()
    if not text:
        raise ValueError("name is required")
    if len(text) > 255:
        raise ValueError("name too long")
    return text


def _clean_id(album_id: str) -> str:
    text = (album_id or "").strip()
    try:
        return str(uuid.UUID(text))
    except ValueError as exc:
        raise ValueError("id must be a UUID") from exc


def create_album(
    session: Session, *, source_id: str, name: str, album_id: str | None = None
) -> Album:
    name = _clean_name(name)
    wanted_id = _clean_id(album_id) if album_id else None
    if wanted_id:
        existing = session.get(Album, wanted_id)
        if existing is not None:
            if existing.source_id != source_id:
                raise FileNotFoundError("album not found")
            if existing.deleted_at is None:
                if existing.name == name:
                    return existing
                raise AlbumConflict("album id already exists")
            other = session.scalar(
                select(Album).where(
                    Album.source_id == source_id,
                    Album.name == name,
                    Album.deleted_at.is_(None),
                    Album.id != wanted_id,
                )
            )
            if other is not None:
                raise AlbumConflict("an album with that name already exists")
            existing.deleted_at = None
            existing.name = name
            existing.cover_asset_id = None
            assign_sync_rev(session, existing)
            return existing
    live = session.scalar(
        select(Album).where(
            Album.source_id == source_id,
            Album.name == name,
            Album.deleted_at.is_(None),
        )
    )
    if live is not None:
        if wanted_id and live.id == wanted_id:
            return live
        raise AlbumConflict("an album with that name already exists")
    tomb = session.scalar(
        select(Album).where(
            Album.source_id == source_id,
            Album.name == name,
            Album.deleted_at.isnot(None),
        )
    )
    if tomb is not None:
        if wanted_id and tomb.id != wanted_id:
            raise AlbumConflict("an album with that name already exists")
        tomb.deleted_at = None
        tomb.cover_asset_id = None
        assign_sync_rev(session, tomb)
        return tomb
    row = Album(id=wanted_id or new_id(), source_id=source_id, name=name)
    assign_sync_rev(session, row)
    session.add(row)
    session.flush()
    return row


def patch_album(
    session: Session,
    album: Album,
    *,
    name: str | None = None,
    cover_asset_id: str | None = None,
    clear_cover: bool = False,
) -> Album:
    if name is not None:
        name = _clean_name(name)
        other = session.scalar(
            select(Album).where(
                Album.source_id == album.source_id,
                Album.name == name,
                Album.deleted_at.is_(None),
                Album.id != album.id,
            )
        )
        if other is not None:
            raise AlbumConflict("an album with that name already exists")
        album.name = name
    if clear_cover:
        album.cover_asset_id = None
    elif cover_asset_id is not None:
        _require_member(session, album.id, cover_asset_id)
        album.cover_asset_id = cover_asset_id
    assign_sync_rev(session, album)
    return album


def delete_album(session: Session, album: Album) -> None:
    now = utcnow()
    links = list(
        session.scalars(
            select(AlbumAsset).where(
                AlbumAsset.album_id == album.id,
                AlbumAsset.deleted_at.is_(None),
            )
        )
    )
    for link in links:
        link.deleted_at = now
        assign_sync_rev(session, link)
    album.deleted_at = now
    assign_sync_rev(session, album)


def add_assets(session: Session, album: Album, assets: list[Asset]) -> Album:
    for asset in assets:
        if asset.source_id != album.source_id or asset.deleted_at is not None:
            raise FileNotFoundError("asset not found")
        _ensure_membership(session, album, asset)
    _touch(album)
    session.flush()
    return album


def remove_asset(session: Session, album: Album, asset_id: str) -> None:
    link = _require_member(session, album.id, asset_id)
    link.deleted_at = utcnow()
    assign_sync_rev(session, link)
    if album.cover_asset_id == asset_id:
        nxt = session.scalar(
            select(AlbumAsset.asset_id)
            .where(
                AlbumAsset.album_id == album.id,
                AlbumAsset.deleted_at.is_(None),
                AlbumAsset.asset_id != asset_id,
            )
            .order_by(AlbumAsset.position.asc(), AlbumAsset.id.asc())
        )
        album.cover_asset_id = nxt
        _touch(album)
    else:
        _touch(album)


def _require_member(session: Session, album_id: str, asset_id: str) -> AlbumAsset:
    link = session.scalar(
        select(AlbumAsset).where(
            AlbumAsset.album_id == album_id,
            AlbumAsset.asset_id == asset_id,
            AlbumAsset.deleted_at.is_(None),
        )
    )
    if link is None:
        raise FileNotFoundError("asset is not in that album")
    return link
