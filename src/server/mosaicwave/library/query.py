from __future__ import annotations

import base64
from datetime import datetime, timezone
from pathlib import PurePosixPath

from pydantic import BaseModel, field_serializer
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from mosaicwave.models import AlbumAsset, Asset, Source


class AssetOut(BaseModel):
    id: str
    filename: str
    relative_path: str
    taken_at: datetime | None
    mime: str | None
    size: int
    width: int | None = None
    height: int | None = None
    thumb_rev: int = 0

    @field_serializer("taken_at")
    def serialize_taken_at(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def asset_to_out(row: Asset) -> AssetOut:
    return AssetOut(
        id=row.id,
        filename=PurePosixPath(row.relative_path).name,
        relative_path=row.relative_path,
        taken_at=row.taken_at,
        mime=row.mime,
        size=row.size,
        width=row.width,
        height=row.height,
        thumb_rev=int(row.thumb_rev or 0),
    )


class AssetListOut(BaseModel):
    items: list[AssetOut]
    next_cursor: str | None = None


def encode_cursor(taken_at: datetime | None, asset_id: str) -> str:
    stamp = taken_at.isoformat() if taken_at else ""
    raw = f"{stamp}|{asset_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime | None, str]:
    padded = cursor + "=" * ((4 - len(cursor) % 4) % 4)
    raw = base64.urlsafe_b64decode(padded).decode()
    stamp, asset_id = raw.split("|", 1)
    taken = datetime.fromisoformat(stamp) if stamp else None
    return taken, asset_id


def list_assets(
    session: Session,
    *,
    limit: int,
    cursor: str | None,
    source_ids: set[str] | None = None,
    album_id: str | None = None,
) -> AssetListOut:
    limit = max(1, min(limit, 200))
    if source_ids is not None and not source_ids:
        return AssetListOut(items=[], next_cursor=None)
    stmt = (
        select(Asset)
        .join(Source, Source.id == Asset.source_id)
        .where(Asset.deleted_at.is_(None), Source.deleted_at.is_(None))
    )
    if source_ids is not None:
        stmt = stmt.where(Asset.source_id.in_(source_ids))
    if album_id:
        stmt = stmt.join(
            AlbumAsset,
            (AlbumAsset.asset_id == Asset.id) & AlbumAsset.deleted_at.is_(None),
        ).where(AlbumAsset.album_id == album_id)
    if cursor:
        taken, asset_id = decode_cursor(cursor)
        if taken is not None:
            stmt = stmt.where(
                (Asset.taken_at < taken)
                | ((Asset.taken_at == taken) & (Asset.id < asset_id))
                | Asset.taken_at.is_(None)
            )
        else:
            stmt = stmt.where(Asset.taken_at.is_(None), Asset.id < asset_id)

    stmt = stmt.order_by(
        case((Asset.taken_at.is_(None), 1), else_=0),
        Asset.taken_at.desc(),
        Asset.id.desc(),
    ).limit(limit + 1)
    rows = list(session.scalars(stmt).all())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = encode_cursor(last.taken_at, last.id)
    items = [asset_to_out(row) for row in rows]
    return AssetListOut(items=items, next_cursor=next_cursor)
