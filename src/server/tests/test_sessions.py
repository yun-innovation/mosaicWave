"""Stored sessions: the list, revoking, expiry, lifetimes an admin sets (doc/architecture.md § App sign-in)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from authutil import ADMIN_PASS, ADMIN_USER, authed_client
from mosaicwave.auth import sessions
from mosaicwave.config import Settings
from mosaicwave.models import UserSession, utcnow


def _server(tmp_path: Path) -> TestClient:
    return authed_client(Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None))


def _app_login(web: TestClient, device: str | None = "Pixel") -> tuple[TestClient, dict[str, str], dict]:
    app = TestClient(web.app)
    res = app.post(
        "/api/v1/auth/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASS, "token": True, "device_name": device},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    return app, {"Authorization": f"Bearer {body['session_token']}"}, body


def _rows(web: TestClient) -> list[UserSession]:
    with web.app.state.session_factory() as db:
        return list(db.scalars(select(UserSession)))


def test_every_sign_in_is_a_stored_row(tmp_path: Path) -> None:
    web = _server(tmp_path)  # setup signed the browser in
    _app_login(web, "Pixel 9")
    rows = _rows(web)
    assert sorted(r.kind for r in rows) == ["app", "browser"]
    assert {r.device_name for r in rows if r.kind == "app"} == {"Pixel 9"}
    assert all(r.revoked_at is None for r in rows)


def test_list_marks_the_current_session_and_hides_other_people_nothing_dead(tmp_path: Path) -> None:
    web = _server(tmp_path)
    app, headers, _ = _app_login(web, "Pixel 9")
    mine = app.get("/api/v1/me/sessions", headers=headers).json()
    assert len(mine) == 2
    assert [row["current"] for row in mine if row["device_name"] == "Pixel 9"] == [True]
    assert [row["kind"] for row in mine if row["current"]] == ["app"]
    assert all(row["created_at"].endswith("Z") and row["expires_at"].endswith("Z") for row in mine)
    assert app.get("/api/v1/me/sessions").status_code == 401


def test_revoking_a_session_ends_it_at_once(tmp_path: Path) -> None:
    web = _server(tmp_path)
    app, headers, _ = _app_login(web)
    app2, headers2, _ = _app_login(web, "Tablet")
    target = next(r for r in app.get("/api/v1/me/sessions", headers=headers).json() if r["device_name"] == "Tablet")
    res = app.delete(f"/api/v1/me/sessions/{target['id']}", headers=headers)
    assert res.status_code == 200, res.text
    assert app2.get("/api/v1/me", headers=headers2).status_code == 401  # the revoked one is out
    assert app.get("/api/v1/me", headers=headers).status_code == 200  # the caller is not
    assert all(r["id"] != target["id"] for r in app.get("/api/v1/me/sessions", headers=headers).json())
    assert app.delete(f"/api/v1/me/sessions/{target['id']}", headers=headers).status_code == 200  # again is fine
    assert app.delete("/api/v1/me/sessions/not-a-session", headers=headers).json()["code"] == "session_not_found"


def test_revoke_others_keeps_only_this_one(tmp_path: Path) -> None:
    web = _server(tmp_path)
    app, headers, _ = _app_login(web)
    _, headers2, _ = _app_login(web, "Tablet")
    res = app.post("/api/v1/me/sessions/revoke-others", headers=headers)
    assert res.json() == {"revoked": 2}  # the browser and the tablet
    assert web.get("/api/v1/me").status_code == 401
    assert app.get("/api/v1/me", headers=headers2).status_code == 401
    assert app.get("/api/v1/me", headers=headers).status_code == 200


def test_logout_revokes_the_session_not_only_the_cookie(tmp_path: Path) -> None:
    web = _server(tmp_path)
    cookie = web.cookies.get("mw_session")
    assert cookie
    assert web.post("/api/v1/auth/logout").status_code == 200
    # Replaying the old cookie value is refused now: the row is revoked.
    assert TestClient(web.app).get("/api/v1/me", headers={"Authorization": f"Bearer {cookie}"}).status_code == 401
    app, headers, _ = _app_login(web)
    assert app.post("/api/v1/auth/logout", headers=headers).status_code == 200
    assert app.get("/api/v1/me", headers=headers).status_code == 401


def test_an_expired_row_ends_the_session_even_with_a_good_signature(tmp_path: Path) -> None:
    web = _server(tmp_path)
    app, headers, _ = _app_login(web)
    with web.app.state.session_factory() as db:
        row = next(r for r in db.scalars(select(UserSession)) if r.kind == "app")
        row.expires_at = utcnow() - timedelta(minutes=1)
        db.commit()
    assert app.get("/api/v1/me", headers=headers).status_code == 401


def test_refresh_moves_the_expiry_and_a_revoked_session_cannot_refresh(tmp_path: Path) -> None:
    web = _server(tmp_path)
    app, headers, _ = _app_login(web)
    with web.app.state.session_factory() as db:
        row = next(r for r in db.scalars(select(UserSession)) if r.kind == "app")
        row.expires_at = utcnow() + timedelta(days=2)
        db.commit()
    res = app.post("/api/v1/auth/refresh", headers=headers)
    assert res.status_code == 200
    soon = next(r for r in _rows(web) if r.kind == "app")
    assert soon.expires_at.replace(tzinfo=None) > (utcnow() + timedelta(days=80)).replace(tzinfo=None)
    assert len([r for r in _rows(web) if r.kind == "app"]) == 1  # the same session, not a new one
    mine = app.get("/api/v1/me/sessions", headers={"Authorization": f"Bearer {res.json()['session_token']}"}).json()
    app.delete(f"/api/v1/me/sessions/{next(r for r in mine if r['current'])['id']}", headers=headers)
    assert app.post("/api/v1/auth/refresh", headers=headers).status_code == 401


def test_admin_sets_the_lifetimes_and_new_sign_ins_use_them(tmp_path: Path) -> None:
    web = _server(tmp_path)
    got = web.get("/api/v1/settings/sessions").json()
    assert got == {"browser_days": 14, "app_days": 90}
    assert web.put("/api/v1/settings/sessions", json={"browser_days": 7, "app_days": 30}).json() == {
        "browser_days": 7,
        "app_days": 30,
    }
    _, _, body = _app_login(web)
    assert body["expires_in"] == 30 * 86400
    web.post("/api/v1/auth/logout")
    res = web.post("/api/v1/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    assert res.status_code == 200
    cookie = res.headers["set-cookie"]
    assert f"Max-Age={7 * 86400}" in cookie
    # A refresh uses the lifetime of the day it happens.
    web.put("/api/v1/settings/sessions", json={"browser_days": 7, "app_days": 60})
    app, headers, _ = _app_login(web)
    assert app.post("/api/v1/auth/refresh", headers=headers).json()["expires_in"] == 60 * 86400


def test_lifetimes_are_checked_and_admin_only(tmp_path: Path) -> None:
    web = _server(tmp_path)
    for bad in (0, 366, -5):
        res = web.put("/api/v1/settings/sessions", json={"browser_days": bad, "app_days": 30})
        assert res.status_code == 400 and res.json()["code"] == "session_days_out_of_range"
    assert web.get("/api/v1/settings/sessions").json()["browser_days"] == 14  # nothing was saved
    assert TestClient(web.app).get("/api/v1/settings/sessions").status_code == 401
    web.put("/api/v1/users", json={"username": "pat", "password": "password1", "role": "member"})
    pat = TestClient(web.app)
    assert pat.post("/api/v1/auth/login", json={"username": "pat", "password": "password1"}).status_code == 200
    assert pat.get("/api/v1/settings/sessions").status_code == 403
    assert pat.put("/api/v1/settings/sessions", json={"browser_days": 30, "app_days": 30}).status_code == 403


def test_lifetime_defaults_and_bounds_helpers(tmp_path: Path) -> None:
    web = _server(tmp_path)
    with web.app.state.session_factory() as db:
        assert sessions.lifetime_days(db, sessions.BROWSER) == 14
        assert sessions.lifetime_days(db, sessions.APP) == 90
        sessions.set_lifetime_days(db, browser=1, app=365)
        assert (sessions.lifetime_days(db, sessions.BROWSER), sessions.lifetime_days(db, sessions.APP)) == (1, 365)
