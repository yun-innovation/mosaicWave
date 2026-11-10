"""App sign-in: a bearer header carries the same signed session value as the cookie (doc/mobile.md)."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from authutil import ADMIN_PASS, ADMIN_USER, authed_client
from mosaicwave.auth.session import sign_session
from mosaicwave.auth.sessions import APP, DEFAULT_DAYS
from mosaicwave.config import Settings

APP_TTL_SEC = DEFAULT_DAYS[APP] * 86400


def _server(tmp_path: Path) -> TestClient:
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    return authed_client(settings)


def _app_client(web: TestClient) -> TestClient:
    """A second client of the same server with no cookies, like a phone app."""
    return TestClient(web.app)


def _app_login(app: TestClient) -> dict:
    res = app.post("/api/v1/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS, "token": True})
    assert res.status_code == 200, res.text
    return res.json()


def test_login_with_token_returns_it_and_sets_no_cookie(tmp_path: Path) -> None:
    app = _app_client(_server(tmp_path))
    res = app.post("/api/v1/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS, "token": True})
    body = res.json()
    assert body["session_token"] and body["expires_in"] == APP_TTL_SEC
    assert body["username"] == ADMIN_USER  # the usual account fields are still there
    assert "set-cookie" not in res.headers
    assert app.get("/api/v1/me").status_code == 401  # no cookie, no header: not signed in


def test_bearer_header_signs_every_request_in(tmp_path: Path) -> None:
    app = _app_client(_server(tmp_path))
    token = _app_login(app)["session_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert app.get("/api/v1/me", headers=headers).json()["username"] == ADMIN_USER
    assert app.get("/api/v1/assets", headers=headers).status_code == 200
    assert app.get("/api/v1/albums", headers=headers).status_code == 200
    assert app.get("/api/v1/auth/status", headers=headers).json()["me"]["username"] == ADMIN_USER
    assert app.get("/api/v1/assets").status_code == 401  # the client keeps no cookie: the header is the only way in


def test_cookie_login_has_no_token_in_the_body(tmp_path: Path) -> None:
    web = _server(tmp_path)
    res = web.post("/api/v1/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    assert res.status_code == 200
    assert res.json()["session_token"] is None and res.json()["expires_in"] is None
    assert web.get("/api/v1/me").status_code == 200  # the cookie still works as before


def test_a_bad_bearer_is_not_rescued_by_the_cookie(tmp_path: Path) -> None:
    web = _server(tmp_path)  # holds a good cookie
    assert web.get("/api/v1/me").status_code == 200
    assert web.get("/api/v1/me", headers={"Authorization": "Bearer nonsense"}).status_code == 401
    secret = web.app.state.session_secret
    expired = sign_session("someone", secret, ttl=60, now=int(time.time()) - 3600)
    assert web.get("/api/v1/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    # Another scheme is not ours, so the cookie decides.
    assert web.get("/api/v1/me", headers={"Authorization": "Basic Zm9vOmJhcg=="}).status_code == 200


def test_refresh_gives_a_working_token_and_needs_a_session(tmp_path: Path) -> None:
    app = _app_client(_server(tmp_path))
    assert app.post("/api/v1/auth/refresh").status_code == 401
    token = _app_login(app)["session_token"]
    res = app.post("/api/v1/auth/refresh", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200, res.text
    fresh = res.json()
    assert fresh["expires_in"] == APP_TTL_SEC and fresh["session_token"]
    assert app.get("/api/v1/me", headers={"Authorization": f"Bearer {fresh['session_token']}"}).status_code == 200
    assert "set-cookie" not in res.headers


def test_a_token_for_a_removed_account_stops_working(tmp_path: Path) -> None:
    web = _server(tmp_path)
    secret = web.app.state.session_secret
    ghost = sign_session("no-such-user", secret, ttl=APP_TTL_SEC)
    assert _app_client(web).get("/api/v1/me", headers={"Authorization": f"Bearer {ghost}"}).status_code == 401
