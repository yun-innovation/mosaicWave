from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from mosaicwave.auth.deps import optional_user, require_admin, require_user
from mosaicwave.auth.access import granted_source_ids
from mosaicwave.auth.passwords import hash_password
from mosaicwave.auth.providers import (
    PROVIDER_LOCAL,
    PROVIDER_QNAP,
    AuthUnavailable,
    create_local_user,
    find_user,
    get_or_create_user,
    qts_sid_from_cookies,
    user_count,
    verify_local,
    verify_qnap_password,
    verify_qnap_sid,
)
from mosaicwave.auth.session import COOKIE_NAME, DEFAULT_TTL_SEC, sign_session
from mosaicwave.config import Settings
from mosaicwave.db import get_session
from mosaicwave.library.ownership import ensure_user_library
from mosaicwave.models import LocalCredential, User

router = APIRouter()

_USERNAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class MeOut(BaseModel):
    id: str
    username: str
    display_name: str
    role: str
    provider: str
    granted_source_ids: list[str] | None = None
    write_source_ids: list[str] | None = None


class AuthStatusOut(BaseModel):
    platform: str
    needs_setup: bool
    me: MeOut | None = None


class LoginIn(BaseModel):
    username: str | None = None
    password: str | None = None


class SetupIn(BaseModel):
    username: str
    password: str
    display_name: str = ""


class UserOut(BaseModel):
    id: str
    username: str
    display_name: str
    role: str
    provider: str


class UserPutIn(BaseModel):
    id: str | None = None
    username: str
    password: str | None = None
    display_name: str = ""
    role: str = "member"


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _normalize_local_username(raw: str) -> str:
    name = raw.strip().lower()
    if not _USERNAME.match(name):
        raise HTTPException(status_code=400, detail="invalid username")
    return name


def _me_out(session: Session, user: User, data_dir: Path | None = None) -> MeOut:
    if data_dir is not None:
        ensure_user_library(session, user, data_dir)
    granted = granted_source_ids(session, user)
    write = granted_source_ids(session, user, write=True)
    return MeOut(
        id=user.id,
        username=user.provider_subject,
        display_name=user.display_name,
        role=user.role,
        provider=user.provider,
        granted_source_ids=sorted(granted),
        write_source_ids=sorted(write),
    )


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.provider_subject,
        display_name=user.display_name,
        role=user.role,
        provider=user.provider,
    )


def _set_session_cookie(request: Request, response: Response, user: User) -> None:
    secret: str = request.app.state.session_secret
    token = sign_session(user.id, secret, ttl=DEFAULT_TTL_SEC)
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=DEFAULT_TTL_SEC,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


@router.get("/auth/status", response_model=AuthStatusOut)
def auth_status(
    request: Request,
    session: Session = Depends(get_session),
    user: User | None = Depends(optional_user),
) -> AuthStatusOut:
    settings = _settings(request)
    needs_setup = settings.platform == "standalone" and user_count(session) == 0
    return AuthStatusOut(
        platform=settings.platform,
        needs_setup=needs_setup,
        me=_me_out(session, user, settings.data_dir) if user else None,
    )


@router.post("/auth/setup", response_model=MeOut)
def auth_setup(
    body: SetupIn,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
) -> MeOut:
    settings = _settings(request)
    if settings.platform != "standalone":
        raise HTTPException(status_code=400, detail="local setup is not available on QNAP")
    if user_count(session) != 0:
        raise HTTPException(status_code=409, detail="already set up")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="password must be at least 8 characters")
    username = _normalize_local_username(body.username)
    try:
        user = create_local_user(
            session,
            username=username,
            password=body.password,
            display_name=body.display_name.strip() or username,
            role="admin",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _set_session_cookie(request, response, user)
    return _me_out(session, user, settings.data_dir)


@router.post("/auth/login", response_model=MeOut)
def auth_login(
    body: LoginIn,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
) -> MeOut:
    settings = _settings(request)
    user: User | None = None
    if settings.platform == "standalone":
        if not body.username or not body.password:
            raise HTTPException(status_code=400, detail="username and password required")
        username = _normalize_local_username(body.username)
        user = verify_local(session, username, body.password)
        if user is None:
            raise HTTPException(status_code=401, detail="invalid credentials")
    else:
        try:
            subject: str | None = None
            if body.username and body.password:
                subject = verify_qnap_password(body.username.strip(), body.password)
            else:
                sid = qts_sid_from_cookies(request.cookies)
                if not sid:
                    raise HTTPException(status_code=400, detail="username and password required")
                subject = verify_qnap_sid(sid)
        except AuthUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if not subject:
            raise HTTPException(status_code=401, detail="invalid credentials")
        user = get_or_create_user(
            session,
            provider=PROVIDER_QNAP,
            subject=subject,
            display_name=subject,
        )
    _set_session_cookie(request, response, user)
    return _me_out(session, user, settings.data_dir)


@router.post("/auth/logout")
def auth_logout(response: Response) -> dict[str, str]:
    _clear_session_cookie(response)
    return {"status": "ok"}


@router.get("/me", response_model=MeOut)
def get_me(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> MeOut:
    return _me_out(session, user, _settings(request).data_dir)


@router.get("/users", response_model=list[UserOut])
def list_users(
    session: Session = Depends(get_session),
    _user: User = Depends(require_user),
) -> list[UserOut]:
    rows = session.scalars(select(User).where(User.deleted_at.is_(None)).order_by(User.provider_subject)).all()
    return [_user_out(row) for row in rows]


@router.put("/users", response_model=UserOut)
def put_user(
    body: UserPutIn,
    request: Request,
    session: Session = Depends(get_session),
    _admin: User = Depends(require_admin),
) -> UserOut:
    settings = _settings(request)
    if body.role not in ("admin", "member"):
        raise HTTPException(status_code=400, detail="role must be admin or member")
    if settings.platform != "standalone":
        raise HTTPException(status_code=400, detail="local users are not used on QNAP")
    username = _normalize_local_username(body.username)
    if body.id:
        user = session.get(User, body.id)
        if user is None or user.deleted_at is not None:
            raise HTTPException(status_code=404, detail="user not found")
        clash = find_user(session, PROVIDER_LOCAL, username)
        if clash is not None and clash.id != user.id:
            raise HTTPException(status_code=409, detail="username already exists")
        user.provider_subject = username
        user.display_name = body.display_name.strip() or username
        user.role = body.role
        if body.password:
            if len(body.password) < 8:
                raise HTTPException(status_code=400, detail="password must be at least 8 characters")
            cred = session.get(LocalCredential, user.id)
            if cred is None:
                session.add(LocalCredential(user_id=user.id, password_hash=hash_password(body.password)))
            else:
                cred.password_hash = hash_password(body.password)
        return _user_out(user)
    if not body.password or len(body.password) < 8:
        raise HTTPException(status_code=400, detail="password must be at least 8 characters")
    try:
        user = create_local_user(
            session,
            username=username,
            password=body.password,
            display_name=body.display_name.strip() or username,
            role=body.role,
        )
    except ValueError as exc:
        detail = str(exc)
        code = 409 if "already exists" in detail else 400
        raise HTTPException(status_code=code, detail=detail) from exc
    return _user_out(user)
