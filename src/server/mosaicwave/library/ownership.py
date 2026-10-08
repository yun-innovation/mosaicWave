"""One private library (source) per user. Others see it only via source_grant."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mosaicwave.models import Source, User, new_id


def library_root(data_dir: Path, user_id: str) -> Path:
    return (data_dir / "libraries" / user_id).resolve()


def user_library(session: Session, user: User) -> Source | None:
    return session.scalar(
        select(Source).where(
            Source.owner_user_id == user.id,
            Source.deleted_at.is_(None),
        )
    )


def ensure_user_library(session: Session, user: User, data_dir: Path) -> Source:
    existing = user_library(session, user)
    if existing is not None:
        Path(existing.root_uri).mkdir(parents=True, exist_ok=True)
        return existing
    root = library_root(data_dir, user.id)
    root.mkdir(parents=True, exist_ok=True)
    source = Source(
        id=new_id(),
        owner_user_id=user.id,
        backend="local",
        root_uri=str(root),
        kind="folder",
    )
    session.add(source)
    session.flush()
    return source
