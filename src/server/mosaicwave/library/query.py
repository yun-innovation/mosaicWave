from __future__ import annotations

import base64
import zoneinfo
from datetime import datetime, timezone
from pathlib import PurePosixPath

from pydantic import BaseModel, field_serializer
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from mosaicwave.models import AlbumAsset, Asset, Source
from mosaicwave.timezone import zone_offsets


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


def _naive_utc(value: datetime) -> datetime:
    """An instant as the naive UTC datetime SQLite stores."""
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def list_assets(
    session: Session,
    *,
    limit: int,
    cursor: str | None,
    source_ids: set[str] | None = None,
    album_id: str | None = None,
    before: datetime | None = None,
    q: str | None = None,
    taken_from: datetime | None = None,
    taken_to: datetime | None = None,
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
    # Search filters hold for every page of the list, cursor or not.
    words = (q or "").strip()
    if words:
        # The path holds the file name and its folders ("Trip to Kyoto/IMG_1.jpg"), so a name or a folder matches.
        stmt = stmt.where(func.lower(Asset.relative_path).contains(words.lower(), autoescape=True))
    if taken_from is not None:
        stmt = stmt.where(Asset.taken_at >= _naive_utc(taken_from))  # undated photos never match a date range
    if taken_to is not None:
        stmt = stmt.where(Asset.taken_at < _naive_utc(taken_to))
    if before is not None and not cursor:
        # Start the list at a point in time (the timeline scrubber): photos taken before `before`,
        # newest first, then the undated ones, which always sort last. `before` is an instant.
        stmt = stmt.where((Asset.taken_at < _naive_utc(before)) | Asset.taken_at.is_(None))
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


class MonthCount(BaseModel):
    month: str  # "2026-03": the UTC month photos were taken in
    count: int


class MonthsOut(BaseModel):
    months: list[MonthCount]  # newest first
    undated: int = 0  # photos with no taken_at; they sort after every month


def month_counts(
    session: Session,
    *,
    source_ids: set[str] | None = None,
    album_id: str | None = None,
    zone: str | None = None,
    use_dst: bool = False,
) -> MonthsOut:
    """Photos per month for the timeline scrubber (the same photos `list_assets` would list).

    Months are in `zone` (a canonical IANA name; None = UTC), the zone the user's page shows days in.
    Without `use_dst` a zone is held at its standard offset all year, which SQLite can do itself; with
    it, a zone that has summer time needs each photo's own offset, so those rows are read and
    bucketed here.
    """
    if source_ids is not None and not source_ids:
        return MonthsOut(months=[])
    python_zone = None
    offset = 0
    if zone and zone != "UTC":
        offset, observes = zone_offsets(zone)
        if observes and use_dst:
            python_zone = zoneinfo.ZoneInfo(zone)
    if python_zone is not None:
        month = Asset.taken_at
    elif offset:
        month = func.strftime("%Y-%m", Asset.taken_at, f"{offset:+d} minutes")
    else:
        month = func.strftime("%Y-%m", Asset.taken_at)
    stmt = (
        select(month, func.count())
        .select_from(Asset)
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
    if python_zone is not None:
        counts: dict[str | None, int] = {}
        for (taken,) in session.execute(stmt.with_only_columns(Asset.taken_at)).all():
            if taken is None:
                key = None
            else:
                if taken.tzinfo is None:
                    taken = taken.replace(tzinfo=timezone.utc)
                key = taken.astimezone(python_zone).strftime("%Y-%m")
            counts[key] = counts.get(key, 0) + 1
        rows = sorted(((k, c) for k, c in counts.items() if k is not None), reverse=True)
        if None in counts:
            rows.append((None, counts[None]))
    else:
        rows = session.execute(stmt.group_by(month).order_by(month.desc())).all()
    undated = sum(count for key, count in rows if key is None)
    return MonthsOut(
        months=[MonthCount(month=key, count=count) for key, count in rows if key is not None],
        undated=undated,
    )
