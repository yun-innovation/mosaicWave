from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_serializer
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from mosaicwave.models import Album, AlbumAsset, Asset, utcnow

KINDS = ("album", "album_asset", "asset")
_CLOCK = "sync_rev_clock"


def max_sync_rev(session: Session) -> int:
    values = [
        session.scalar(select(func.max(Asset.rev))) or 0,
        session.scalar(select(func.max(Album.rev))) or 0,
        session.scalar(select(func.max(AlbumAsset.rev))) or 0,
    ]
    return int(max(values))


def next_sync_rev(session: Session) -> int:
    """Library-wide clock so `since_rev` pull can see new rows, not only per-row bumps."""
    clock = session.info.get(_CLOCK)
    if clock is None:
        clock = max_sync_rev(session)
    clock = int(clock) + 1
    session.info[_CLOCK] = clock
    return clock


def assign_sync_rev(session: Session, row: Asset | Album | AlbumAsset) -> None:
    row.rev = next_sync_rev(session)
    row.updated_at = utcnow()


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _extra(raw: str | None) -> dict | list | str | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    return data


def encode_sync_cursor(rev: int, kind: str, row_id: str) -> str:
    raw = f"{rev}|{kind}|{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_sync_cursor(cursor: str) -> tuple[int, str, str]:
    padded = cursor + "=" * ((4 - len(cursor) % 4) % 4)
    raw = base64.urlsafe_b64decode(padded).decode()
    rev_s, kind, row_id = raw.split("|", 2)
    if kind not in KINDS:
        raise ValueError("invalid cursor kind")
    return int(rev_s), kind, row_id


def _after_cursor(rev_col, id_col, kind: str, since_rev: int, cursor: tuple[int, str, str] | None):
    if cursor is None:
        return rev_col > since_rev
    c_rev, c_kind, c_id = cursor
    if kind > c_kind:
        return rev_col >= c_rev
    if kind == c_kind:
        return (rev_col > c_rev) | ((rev_col == c_rev) & (id_col > c_id))
    return rev_col > c_rev


class SyncAssetOut(BaseModel):
    id: str
    source_id: str
    relative_path: str
    size: int
    mime: str | None
    width: int | None
    height: int | None
    taken_at: datetime | None
    duration: float | None = None
    extra: dict | list | str | None = None
    content_hash: str | None = None
    thumb_rev: int = 0
    rev: int
    updated_at: datetime
    deleted_at: datetime | None = None

    @field_serializer("taken_at", "updated_at", "deleted_at")
    def serialize_dt(self, value: datetime | None) -> str | None:
        return _iso(value)


class SyncAlbumOut(BaseModel):
    id: str
    name: str
    cover_asset_id: str | None
    sort: int
    rev: int
    updated_at: datetime
    deleted_at: datetime | None = None

    @field_serializer("updated_at", "deleted_at")
    def serialize_dt(self, value: datetime | None) -> str | None:
        return _iso(value)


class SyncAlbumAssetOut(BaseModel):
    id: str
    album_id: str
    asset_id: str
    position: int
    rev: int
    updated_at: datetime
    deleted_at: datetime | None = None

    @field_serializer("updated_at", "deleted_at")
    def serialize_dt(self, value: datetime | None) -> str | None:
        return _iso(value)


class SyncChangesOut(BaseModel):
    server_rev: int
    next_cursor: str | None = None
    assets: list[SyncAssetOut] = Field(default_factory=list)
    albums: list[SyncAlbumOut] = Field(default_factory=list)
    album_assets: list[SyncAlbumAssetOut] = Field(default_factory=list)


def _asset_out(row: Asset) -> SyncAssetOut:
    return SyncAssetOut(
        id=row.id,
        source_id=row.source_id,
        relative_path=row.relative_path,
        size=int(row.size or 0),
        mime=row.mime,
        width=row.width,
        height=row.height,
        taken_at=row.taken_at,
        duration=row.duration,
        extra=_extra(row.extra_json),
        content_hash=row.content_hash,
        thumb_rev=int(row.thumb_rev or 0),
        rev=int(row.rev),
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
    )


def _album_out(row: Album) -> SyncAlbumOut:
    return SyncAlbumOut(
        id=row.id,
        name=row.name,
        cover_asset_id=row.cover_asset_id,
        sort=int(row.sort or 0),
        rev=int(row.rev),
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
    )


def _link_out(row: AlbumAsset) -> SyncAlbumAssetOut:
    return SyncAlbumAssetOut(
        id=row.id,
        album_id=row.album_id,
        asset_id=row.asset_id,
        position=int(row.position or 0),
        rev=int(row.rev),
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
    )


def _visible_asset_ids(session: Session, source_ids: set[str] | None):
    stmt = select(Asset.id)
    if source_ids is not None:
        stmt = stmt.where(Asset.source_id.in_(source_ids))
    return stmt


def pull_changes(
    session: Session,
    *,
    since_rev: int,
    cursor: str | None,
    limit: int,
    source_ids: set[str] | None,
) -> SyncChangesOut:
    limit = max(1, min(limit, 500))
    since_rev = max(0, since_rev)
    if source_ids is not None and not source_ids:
        return SyncChangesOut(server_rev=since_rev)
    parsed: tuple[int, str, str] | None = None
    if cursor:
        parsed = decode_sync_cursor(cursor)

    visible = _visible_asset_ids(session, source_ids)
    take = limit + 1

    asset_stmt = select(Asset).where(_after_cursor(Asset.rev, Asset.id, "asset", since_rev, parsed))
    if source_ids is not None:
        asset_stmt = asset_stmt.where(Asset.source_id.in_(source_ids))
    asset_stmt = asset_stmt.order_by(Asset.rev.asc(), Asset.id.asc()).limit(take)

    link_stmt = (
        select(AlbumAsset)
        .where(
            _after_cursor(AlbumAsset.rev, AlbumAsset.id, "album_asset", since_rev, parsed),
            AlbumAsset.asset_id.in_(visible),
        )
        .order_by(AlbumAsset.rev.asc(), AlbumAsset.id.asc())
        .limit(take)
    )
    album_cond = Album.id.in_(select(AlbumAsset.album_id).where(AlbumAsset.asset_id.in_(visible)))
    if source_ids is not None:
        album_cond = or_(album_cond, Album.source_id.in_(source_ids))
        album_cond = album_cond & (Album.source_id.is_(None) | Album.source_id.in_(source_ids))
    album_stmt = (
        select(Album)
        .where(
            _after_cursor(Album.rev, Album.id, "album", since_rev, parsed),
            album_cond,
        )
        .order_by(Album.rev.asc(), Album.id.asc())
        .limit(take)
    )

    merged: list[tuple[int, str, str, object]] = []
    for row in session.scalars(asset_stmt):
        merged.append((int(row.rev), "asset", row.id, row))
    for row in session.scalars(link_stmt):
        merged.append((int(row.rev), "album_asset", row.id, row))
    for row in session.scalars(album_stmt):
        merged.append((int(row.rev), "album", row.id, row))
    merged.sort(key=lambda item: (item[0], item[1], item[2]))

    next_cursor = None
    if len(merged) > limit:
        merged = merged[:limit]
        last = merged[-1]
        next_cursor = encode_sync_cursor(last[0], last[1], last[2])

    assets: list[SyncAssetOut] = []
    albums: list[SyncAlbumOut] = []
    album_assets: list[SyncAlbumAssetOut] = []
    server_rev = since_rev
    for rev, kind, _row_id, row in merged:
        server_rev = max(server_rev, rev)
        if kind == "asset":
            assets.append(_asset_out(row))
        elif kind == "album":
            albums.append(_album_out(row))
        else:
            album_assets.append(_link_out(row))
    return SyncChangesOut(
        server_rev=server_rev,
        next_cursor=next_cursor,
        assets=assets,
        albums=albums,
        album_assets=album_assets,
    )
