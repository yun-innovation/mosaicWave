from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from mosaicwave.library.ownership import user_library
from mosaicwave.models import Source, SourceGrant, User, new_id, utcnow


def is_admin(user: User) -> bool:
    return user.role == "admin"


def granted_source_ids(session: Session, user: User, *, write: bool = False) -> set[str]:
    """Own library plus live grants. Owner is always write. Admin has no extra bypass."""
    ids: set[str] = set()
    lib = user_library(session, user)
    if lib is not None:
        ids.add(lib.id)
    wanted = ("write",) if write else ("read", "write")
    rows = session.scalars(
        select(SourceGrant).where(
            SourceGrant.user_id == user.id,
            SourceGrant.deleted_at.is_(None),
            SourceGrant.perm.in_(wanted),
        )
    )
    for grant in rows:
        source = session.get(Source, grant.source_id)
        if source is None or source.deleted_at is not None:
            continue
        ids.add(grant.source_id)
    return ids


def grant_perm(session: Session, user: User, source_id: str) -> str | None:
    lib = user_library(session, user)
    if lib is not None and lib.id == source_id:
        return "write"
    grant = session.scalar(
        select(SourceGrant).where(
            SourceGrant.user_id == user.id,
            SourceGrant.source_id == source_id,
            SourceGrant.deleted_at.is_(None),
        )
    )
    if grant is None or grant.perm not in ("read", "write"):
        return None
    source = session.get(Source, source_id)
    if source is None or source.deleted_at is not None:
        return None
    return grant.perm


def can_read_source(session: Session, user: User, source_id: str) -> bool:
    return grant_perm(session, user, source_id) is not None


def can_write_source(session: Session, user: User, source_id: str) -> bool:
    return grant_perm(session, user, source_id) == "write"


def list_live_grants(session: Session, source_id: str) -> list[SourceGrant]:
    return list(
        session.scalars(
            select(SourceGrant)
            .where(
                SourceGrant.source_id == source_id,
                SourceGrant.deleted_at.is_(None),
            )
            .order_by(SourceGrant.user_id.asc())
        )
    )


def replace_source_grants(
    session: Session, source: Source, items: list[tuple[str, str]]
) -> list[SourceGrant]:
    wanted: dict[str, str] = {}
    for user_id, perm in items:
        user_id = (user_id or "").strip()
        perm = (perm or "").strip()
        if perm not in ("read", "write"):
            raise ValueError("perm must be read or write")
        if not user_id:
            raise ValueError("user_id is required")
        if user_id in wanted:
            raise ValueError("duplicate user_id in grants")
        if user_id == source.owner_user_id:
            raise ValueError("cannot grant to the library owner")
        target = session.get(User, user_id)
        if target is None or target.deleted_at is not None:
            raise FileNotFoundError("user not found")
        wanted[user_id] = perm

    existing = list(
        session.scalars(select(SourceGrant).where(SourceGrant.source_id == source.id))
    )
    by_user = {row.user_id: row for row in existing}
    now = utcnow()
    for user_id, perm in wanted.items():
        row = by_user.get(user_id)
        if row is None:
            session.add(
                SourceGrant(
                    id=new_id(),
                    user_id=user_id,
                    source_id=source.id,
                    perm=perm,
                )
            )
            continue
        row.perm = perm
        row.deleted_at = None
        row.updated_at = now
        row.rev = int(row.rev or 0) + 1
    for row in existing:
        if row.user_id not in wanted and row.deleted_at is None:
            row.deleted_at = now
            row.updated_at = now
            row.rev = int(row.rev or 0) + 1
    session.flush()
    return list_live_grants(session, source.id)
