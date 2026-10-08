from __future__ import annotations

import hmac
import hashlib
import time

COOKIE_NAME = "mw_session"
DEFAULT_TTL_SEC = 14 * 24 * 60 * 60


def sign_session(user_id: str, secret: str, *, ttl: int = DEFAULT_TTL_SEC, now: int | None = None) -> str:
    exp = int(now if now is not None else time.time()) + int(ttl)
    payload = f"{user_id}.{exp}"
    sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def read_session(token: str, secret: str, *, now: int | None = None) -> str | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    user_id, exp_s, sig = parts
    if not user_id or not exp_s or not sig:
        return None
    payload = f"{user_id}.{exp_s}"
    expected = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        exp = int(exp_s)
    except ValueError:
        return None
    if exp < int(now if now is not None else time.time()):
        return None
    return user_id
