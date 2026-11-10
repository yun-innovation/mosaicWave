from __future__ import annotations

import hmac
import hashlib
import time

COOKIE_NAME = "mw_session"

# The subject of a signed value is a user_session id (auth/sessions.py); lifetimes are admin settings.


def sign_session(subject: str, secret: str, *, ttl: int, now: int | None = None) -> str:
    exp = int(now if now is not None else time.time()) + int(ttl)
    payload = f"{subject}.{exp}"
    sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def read_session(token: str, secret: str, *, now: int | None = None) -> str | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    subject, exp_s, sig = parts
    if not subject or not exp_s or not sig:
        return None
    payload = f"{subject}.{exp_s}"
    expected = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        exp = int(exp_s)
    except ValueError:
        return None
    if exp < int(now if now is not None else time.time()):
        return None
    return subject
