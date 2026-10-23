from __future__ import annotations

from sqlalchemy.orm import Session

from mosaicwave.library.ownership import user_library
from mosaicwave.models import User


def is_admin(user: User) -> bool:
    return user.role == "admin"


def granted_source_ids(session: Session, user: User) -> set[str]:
    """Sources this user may use. `session` is the user's own library database, which holds
    only their library, so the answer is that library (read and write) or nothing yet."""
    lib = user_library(session, user)
    return {lib.id} if lib is not None else set()


def can_use_source(session: Session, user: User, source_id: str) -> bool:
    return source_id in granted_source_ids(session, user)
