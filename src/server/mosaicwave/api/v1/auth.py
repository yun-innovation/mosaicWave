from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from mosaicwave.api.errors import ApiError, wrapped
from mosaicwave.api.v1.maintenance import refuse_non_admin
from mosaicwave.auth.deps import optional_user, require_admin, require_user
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
from mosaicwave.auth.session import APP_TTL_SEC, COOKIE_NAME, DEFAULT_TTL_SEC, sign_session
from mosaicwave.config import Settings
from mosaicwave.db import get_session
from mosaicwave.i18n import LOCALES, normalize_locale
from mosaicwave.library.ownership import ensure_user_library
from mosaicwave.models import LocalCredential, User, utcnow
from mosaicwave.timezone import canonical_zone, tzdata_version, zone_choices, zone_data_available

router = APIRouter()

_USERNAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
MIN_PASSWORD = 8


class MeOut(BaseModel):
    id: str
    username: str
    display_name: str
    role: str
    provider: str
    locale: str | None = None
    time_zone: str | None = None  # IANA name; null = UTC, the default
    use_dst: bool = False  # follow the zone's summer time; false = its standard time all year


class MePatchIn(BaseModel):
    locale: str | None = None
    time_zone: str | None = None
    use_dst: bool | None = None  # null is ignored: it is a switch, not a choice that can be cleared


class AuthStatusOut(BaseModel):
    platform: str
    needs_setup: bool
    me: MeOut | None = None
    locales: list[str] = list(LOCALES)  # the languages this server can answer and save in


class LoginIn(BaseModel):
    username: str | None = None
    password: str | None = None
    token: bool = False  # an app: answer with the session value in the body, set no cookie


class LoginOut(MeOut):
    session_token: str | None = None  # only when `token` was asked for; send it as `Authorization: Bearer`
    expires_in: int | None = None  # seconds the token is good for


class RefreshOut(BaseModel):
    session_token: str
    expires_in: int


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
        raise ApiError(400, "invalid_username", "invalid username")
    return name


def _password_too_short() -> ApiError:
    return ApiError(
        400,
        "password_too_short",
        f"password must be at least {MIN_PASSWORD} characters",
        {"min": MIN_PASSWORD},
    )


def _me_out(request: Request, user: User) -> MeOut:
    # Opening the library database also creates the caller's library on first login.
    library = request.app.state.library_dbs.factory(user.id)()
    try:
        ensure_user_library(library, user, _settings(request).data_dir)
        library.commit()
    finally:
        library.close()
    return MeOut(
        id=user.id,
        username=user.provider_subject,
        display_name=user.display_name,
        role=user.role,
        provider=user.provider,
        locale=user.locale,
        time_zone=user.time_zone,
        use_dst=bool(user.use_dst),
    )


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.provider_subject,
        display_name=user.display_name,
        role=user.role,
        provider=user.provider,
    )


def _credentials_required() -> ApiError:
    return ApiError(400, "credentials_required", "username and password required")


def _invalid_credentials() -> ApiError:
    return ApiError(401, "invalid_credentials", "invalid credentials")


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
        me=_me_out(request, user) if user else None,
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
        raise ApiError(400, "setup_not_available", "local setup is not available on QNAP")
    if user_count(session) != 0:
        raise ApiError(409, "already_set_up", "already set up")
    if len(body.password) < MIN_PASSWORD:
        raise _password_too_short()
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
        raise wrapped(400, "user_rejected", exc) from exc
    _set_session_cookie(request, response, user)
    return _me_out(request, user)


@router.post("/auth/login", response_model=LoginOut)
def auth_login(
    body: LoginIn,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
) -> LoginOut:
    settings = _settings(request)
    user: User | None = None
    if settings.platform == "standalone":
        if not body.username or not body.password:
            raise _credentials_required()
        username = _normalize_local_username(body.username)
        user = verify_local(session, username, body.password)
        if user is None:
            raise _invalid_credentials()
    else:
        try:
            subject: str | None = None
            if body.username and body.password:
                subject = verify_qnap_password(body.username.strip(), body.password)
            else:
                sid = qts_sid_from_cookies(request.cookies)
                if not sid:
                    raise _credentials_required()
                subject = verify_qnap_sid(sid)
        except AuthUnavailable as exc:
            raise wrapped(503, "qnap_auth_unavailable", exc) from exc
        if not subject:
            raise _invalid_credentials()
        user = get_or_create_user(
            session,
            provider=PROVIDER_QNAP,
            subject=subject,
            display_name=subject,
        )
    request.state.user_locale = user.locale
    refuse_non_admin(settings, user)
    me = LoginOut(**_me_out(request, user).model_dump())
    if body.token:
        me.session_token = sign_session(user.id, request.app.state.session_secret, ttl=APP_TTL_SEC)
        me.expires_in = APP_TTL_SEC
    else:
        _set_session_cookie(request, response, user)
    return me


@router.post("/auth/refresh", response_model=RefreshOut)
def auth_refresh(request: Request, user: User = Depends(require_user)) -> RefreshOut:
    """A fresh app session value while the current one (bearer header or cookie) is still good.

    Sessions are signed, not stored, so the old value stays valid until it expires; there is no
    per-session revoke (doc/mobile.md § Open questions). Removing the account or changing the
    server secret ends every session.
    """
    token = sign_session(user.id, request.app.state.session_secret, ttl=APP_TTL_SEC)
    return RefreshOut(session_token=token, expires_in=APP_TTL_SEC)


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
    return _me_out(request, user)


class TimeZonesOut(BaseModel):
    version: str | None = None  # the IANA tzdata release the names come from, e.g. 2026f
    zones: list[str]  # "UTC" first, then every real place A to Z; the web offers exactly these

@router.get("/time-zones", response_model=TimeZonesOut)
def get_time_zones(_user: User = Depends(require_user)) -> TimeZonesOut:
    """The zones a user can pick for ``PATCH /me``, from the server's own ``tzdata``.

    One list for every client, so the web and the server never disagree about a name. Legacy aliases
    (``US/Pacific``) are not listed but are still accepted by ``PATCH /me``.
    """
    return TimeZonesOut(version=tzdata_version(), zones=zone_choices())


@router.patch("/me", response_model=MeOut)
def patch_me(
    body: MePatchIn,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> MeOut:
    zone: str | None = None
    if "time_zone" in body.model_fields_set and body.time_zone is not None:
        # Checked first, so a bad zone saves nothing, not even the locale sent with it.
        if not zone_data_available():
            # Not the user's mistake: say so, instead of calling every real zone unknown.
            raise ApiError(
                503,
                "time_zone_data_missing",
                "this server has no time zone database; install the tzdata package "
                "(pip install tzdata, or pip install -e .[dev] again) and restart",
            )
        zone = canonical_zone(body.time_zone)
        if zone is None:
            raise ApiError(
                400,
                "invalid_time_zone",
                f"unknown time zone: {body.time_zone}",
                {"time_zone": body.time_zone},
            )
    if "locale" in body.model_fields_set:
        # A wide tag (zh-TW, en-GB) is saved as the supported code it maps to; only the
        # canonical code is stored or returned. An unsupported target is refused, and nothing
        # is saved. The language the request names for its answer is never saved.
        target = None
        if body.locale is not None:
            target = normalize_locale(body.locale)
            if target is None:
                raise ApiError(
                    400,
                    "invalid_locale",
                    f"locale must be one of {', '.join(LOCALES)} or null",
                    {"locale": body.locale},
                )
        user.locale = target
        user.updated_at = utcnow()
        session.flush()
        request.state.user_locale = target
        if target is not None:
            request.state.language = target  # this answer already comes in the new language
    if "time_zone" in body.model_fields_set:
        user.time_zone = zone  # None clears it: UTC
        user.updated_at = utcnow()
        session.flush()
    if body.use_dst is not None:
        user.use_dst = body.use_dst
        user.updated_at = utcnow()
        session.flush()
    return _me_out(request, user)


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
        raise ApiError(400, "invalid_role", "role must be admin or member")
    if settings.platform != "standalone":
        raise ApiError(400, "local_users_unavailable", "local users are not used on QNAP")
    username = _normalize_local_username(body.username)
    if body.id:
        user = session.get(User, body.id)
        if user is None or user.deleted_at is not None:
            raise ApiError(404, "user_not_found", "user not found")
        clash = find_user(session, PROVIDER_LOCAL, username)
        if clash is not None and clash.id != user.id:
            raise ApiError(409, "username_taken", "username already exists")
        user.provider_subject = username
        user.display_name = body.display_name.strip() or username
        user.role = body.role
        if body.password:
            if len(body.password) < MIN_PASSWORD:
                raise _password_too_short()
            cred = session.get(LocalCredential, user.id)
            if cred is None:
                session.add(LocalCredential(user_id=user.id, password_hash=hash_password(body.password)))
            else:
                cred.password_hash = hash_password(body.password)
        return _user_out(user)
    if not body.password or len(body.password) < MIN_PASSWORD:
        raise _password_too_short()
    try:
        user = create_local_user(
            session,
            username=username,
            password=body.password,
            display_name=body.display_name.strip() or username,
            role=body.role,
        )
    except ValueError as exc:
        if "already exists" in str(exc):
            raise ApiError(409, "username_taken", "username already exists") from exc
        raise wrapped(400, "user_rejected", exc) from exc
    return _user_out(user)
