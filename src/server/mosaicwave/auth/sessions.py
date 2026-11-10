"""Stored sign-ins. The session value a client sends (cookie or bearer header) is signed and names a row of
``user_session``; the row decides: not revoked, not expired, and its user still exists (doc/architecture.md
§ App sign-in). Lifetimes are admin settings kept in ``site_setting``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from mosaicwave.auth.session import read_session, sign_session
from mosaicwave.models import SiteSetting, User, UserSession, utcnow

BROWSER = "browser"
APP = "app"
KINDS = (BROWSER, APP)

DEFAULT_DAYS = {BROWSER: 14, APP: 90}
MIN_DAYS = 1
MAX_DAYS = 365
_KEYS = {BROWSER: "session_days_browser", APP: "session_days_app"}

# `last_seen_at` is written at most this often, so a busy client does not write on every request.
TOUCH_EVERY = timedelta(minutes=5)
DEVICE_NAME_MAX = 100


def _naive(value: datetime) -> datetime:
    """SQLite hands datetimes back naive (UTC); compare like with like."""
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def _now() -> datetime:
    return _naive(utcnow())


def lifetime_days(session: Session, kind: str) -> int:
    row = session.get(SiteSetting, _KEYS[kind])
    if row is not None:
        try:
            days = int(row.value)
        except ValueError:
            return DEFAULT_DAYS[kind]
        if MIN_DAYS <= days <= MAX_DAYS:
            return days
    return DEFAULT_DAYS[kind]


def set_lifetime_days(session: Session, *, browser: int, app: int) -> None:
    for kind, days in ((BROWSER, browser), (APP, app)):
        row = session.get(SiteSetting, _KEYS[kind])
        if row is None:
            session.add(SiteSetting(key=_KEYS[kind], value=str(days)))
        else:
            row.value = str(days)
            row.updated_at = utcnow()
    session.commit()


def clean_device_name(raw: str | None) -> str | None:
    name = " ".join((raw or "").split())[:DEVICE_NAME_MAX]
    return name or None


def issue(session: Session, secret: str, user: User, kind: str, device_name: str | None) -> tuple[str, int]:
    """Start a session for ``user``. Returns the value to send and the seconds it is good for."""
    delete_dead(session)  # sign-ins are rare, so this is a cheap place to tidy
    ttl = lifetime_days(session, kind) * 86400
    now = _now()
    row = UserSession(
        user_id=user.id,
        kind=kind,
        device_name=clean_device_name(device_name),
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(seconds=ttl),
    )
    session.add(row)
    session.commit()
    return sign_session(row.id, secret, ttl=ttl), ttl


def renew(session: Session, secret: str, row: UserSession) -> tuple[str, int]:
    """A fresh value for an existing session, with its expiry moved to now plus the current lifetime."""
    ttl = lifetime_days(session, row.kind) * 86400
    row.expires_at = _now() + timedelta(seconds=ttl)
    session.commit()
    return sign_session(row.id, secret, ttl=ttl), ttl


def resolve(session: Session, token: str, secret: str, *, touch: bool = True) -> tuple[UserSession, User] | None:
    """The live session and user a signed value stands for, or None."""
    session_id = read_session(token, secret)
    if not session_id:
        return None
    row = session.get(UserSession, session_id)
    if row is None or row.revoked_at is not None:
        return None
    now = _now()
    if _naive(row.expires_at) <= now:
        return None
    user = session.get(User, row.user_id)
    if user is None or user.deleted_at is not None:
        return None
    if touch and now - _naive(row.last_seen_at) >= TOUCH_EVERY:
        row.last_seen_at = now
        session.commit()
    return row, user


def revoke(session: Session, row: UserSession) -> None:
    if row.revoked_at is None:
        row.revoked_at = _now()
        session.commit()


def live_sessions(session: Session, user_id: str) -> list[UserSession]:
    """The user's sessions that still work, newest sign-in first."""
    now = _now()
    rows = session.query(UserSession).filter(UserSession.user_id == user_id, UserSession.revoked_at.is_(None)).all()
    live = [row for row in rows if _naive(row.expires_at) > now]
    return sorted(live, key=lambda row: _naive(row.created_at), reverse=True)


def delete_dead(session: Session) -> int:
    """Drop rows that expired or were revoked more than a day ago (called now and then, never needed to be correct)."""
    cutoff = _now() - timedelta(days=1)
    rows = session.query(UserSession).filter(
        (UserSession.expires_at < cutoff) | (UserSession.revoked_at.is_not(None) & (UserSession.revoked_at < cutoff))
    )
    count = rows.delete(synchronize_session=False)
    session.commit()
    return count
