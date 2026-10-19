from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from mosaicwave.api.errors import ApiError
from mosaicwave.auth.session import COOKIE_NAME, read_session
from mosaicwave.db import get_session
from mosaicwave.models import User


def _user_id_from_cookie(request: Request) -> str | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    secret: str = request.app.state.session_secret
    return read_session(token, secret)


def optional_user(
    request: Request,
    session: Session = Depends(get_session),
) -> User | None:
    user_id = _user_id_from_cookie(request)
    if not user_id:
        return None
    user = session.get(User, user_id)
    if user is None or user.deleted_at is not None:
        return None
    return user


def require_user(user: User | None = Depends(optional_user)) -> User:
    if user is None:
        raise ApiError(401, "not_authenticated", "not authenticated")
    return user


def require_user_brief(request: Request) -> User:
    """Load the caller and close SQLite before a long body (Takeout device upload / finish)."""
    user_id = _user_id_from_cookie(request)
    if not user_id:
        raise ApiError(401, "not_authenticated", "not authenticated")
    factory = request.app.state.session_factory
    session = factory()
    try:
        user = session.get(User, user_id)
        if user is None or user.deleted_at is not None:
            raise ApiError(401, "not_authenticated", "not authenticated")
        session.expunge(user)
        return user
    finally:
        session.close()


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "admin":
        raise ApiError(403, "admin_only", "admin only")
    return user
