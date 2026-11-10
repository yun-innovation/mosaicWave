from __future__ import annotations

from collections.abc import Generator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from mosaicwave.api.errors import ApiError
from mosaicwave.auth.session import COOKIE_NAME
from mosaicwave.auth.sessions import resolve
from mosaicwave.db import get_session
from mosaicwave.models import User, UserSession


def bearer_token(request: Request) -> str | None:
    """The value of ``Authorization: Bearer <value>``, or None when the header is absent or another scheme."""
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    value = value.strip()
    return value if scheme.lower() == "bearer" and value else None


def session_token(request: Request) -> str | None:
    """The signed session value a request carries: the bearer header (apps) when sent, else the cookie (browsers).

    A bearer header that does not verify is not rescued by a cookie, so a client never acts as someone it
    did not mean to.
    """
    header = bearer_token(request)
    return header if header is not None else request.cookies.get(COOKIE_NAME)


def current_login(
    request: Request, session: Session, *, touch: bool = True, cache: bool = True
) -> tuple[UserSession, User] | None:
    """The live session and user behind this request, looked up once per request.

    ``cache=False`` is for a caller that closes its session right after (the objects would outlive it).
    """
    cached = getattr(request.state, "login", None) if cache else None
    if cached is not None:
        return cached
    token = session_token(request)
    if not token:
        return None
    login = resolve(session, token, request.app.state.session_secret, touch=touch)
    if login is not None and cache:
        request.state.login = login
    return login


def optional_user(
    request: Request,
    session: Session = Depends(get_session),
) -> User | None:
    login = current_login(request, session)
    if login is None:
        return None
    user = login[1]
    request.state.user_locale = user.locale  # the answer language when the request names none
    return user


def require_user(user: User | None = Depends(optional_user)) -> User:
    if user is None:
        raise ApiError(401, "not_authenticated", "not authenticated")
    return user


def require_user_brief(request: Request) -> User:
    """Load the caller and close SQLite before a long body (Takeout device upload / finish)."""
    factory = request.app.state.session_factory
    session = factory()
    try:
        login = current_login(request, session, cache=False)
        if login is None:
            raise ApiError(401, "not_authenticated", "not authenticated")
        user = login[1]
        session.expunge(user)
        request.state.user_locale = user.locale
        return user
    finally:
        session.close()

def get_library_session(
    request: Request, user: User = Depends(require_user)
) -> Generator[Session, None, None]:
    """The caller's own library database (accounts/{user_id}/library.db), created on first use."""
    factory = request.app.state.library_dbs.factory(user.id)
    session = factory()
    try:
        yield session
        if session.is_active:
            session.commit()
    except Exception:
        if session.is_active:
            session.rollback()
        raise
    finally:
        session.close()


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "admin":
        raise ApiError(403, "admin_only", "admin only")
    return user
