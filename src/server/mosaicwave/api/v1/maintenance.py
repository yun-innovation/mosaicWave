"""Maintenance mode: public status, admin switch, and the guard that keeps everyone else out."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from mosaicwave import maintenance
from mosaicwave.api.errors import ApiError
from mosaicwave.auth.deps import _user_id_from_cookie, require_admin
from mosaicwave.db import get_session
from mosaicwave.models import Job, User

router = APIRouter()

API_PREFIX = "/api/v1/"
# Reachable by everyone during maintenance: liveness, the status the alert page polls, and sign-in
# (login itself refuses non-admins once it knows who they are).
OPEN_PATHS = frozenset(
    {
        "/api/v1/health",
        "/api/v1/maintenance",
        "/api/v1/auth/status",
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
    }
)
RETRY_AFTER_SECONDS = "10"


class MaintenanceIn(BaseModel):
    enabled: bool
    reason: str = Field(default="", max_length=maintenance.MAX_REASON)


class MaintenanceOut(BaseModel):
    state: str = Field(examples=["off", "draining", "on"])
    reason: str = ""
    since: str | None = None


def job_running(session: Session) -> bool:
    return session.scalar(select(Job.id).where(Job.status == "running").limit(1)) is not None


def maintenance_error(flag: maintenance.Flag) -> ApiError:
    return ApiError(
        503,
        "maintenance",
        "The server is in maintenance mode",
        {"reason": flag.reason, "since": flag.since},
        headers={"Retry-After": RETRY_AFTER_SECONDS},
    )


def refuse_non_admin(settings, user: User) -> None:  # type: ignore[no-untyped-def]
    """Sign-in check: while maintenance is on, only admins get a session."""
    flag = maintenance.read(settings)
    if flag is not None and user.role != "admin":
        raise maintenance_error(flag)


def _out(session: Session, settings) -> MaintenanceOut:  # type: ignore[no-untyped-def]
    flag = maintenance.read(settings)
    if flag is None:
        return MaintenanceOut(state=maintenance.OFF)
    state = maintenance.state_of(flag, job_running(session))
    return MaintenanceOut(state=state, reason=flag.reason, since=flag.since or None)


def maintenance_guard(request: Request) -> None:
    """App-wide dependency: during maintenance only admins may call the API."""
    settings = request.app.state.settings
    flag = maintenance.read(settings)
    if flag is None:
        return
    path = request.url.path
    if not path.startswith(API_PREFIX) or path in OPEN_PATHS:
        return
    user_id = _user_id_from_cookie(request)
    if user_id:
        session = request.app.state.session_factory()
        try:
            user = session.get(User, user_id)
            if user is not None and user.deleted_at is None and user.role == "admin":
                return
        finally:
            session.close()
    raise maintenance_error(flag)


@router.get("/maintenance", response_model=MaintenanceOut)
def get_maintenance(request: Request, session: Session = Depends(get_session)) -> MaintenanceOut:
    return _out(session, request.app.state.settings)


@router.put("/maintenance", response_model=MaintenanceOut)
def put_maintenance(
    body: MaintenanceIn,
    request: Request,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> MaintenanceOut:
    settings = request.app.state.settings
    if body.enabled:
        maintenance.enable(settings, body.reason, by=admin.id)
    else:
        maintenance.disable(settings)
    return _out(session, settings)
