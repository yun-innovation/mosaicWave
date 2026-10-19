from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text

from mosaicwave.config import Settings
from mosaicwave.db import make_engine
from mosaicwave.main import create_app
from authutil import ADMIN_PASS, ADMIN_USER, authed_client


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)


def test_locale_defaults_to_null(tmp_path: Path) -> None:
    client = authed_client(_settings(tmp_path))
    assert client.get("/api/v1/me").json()["locale"] is None
    assert client.get("/api/v1/auth/status").json()["me"]["locale"] is None


def test_patch_me_sets_and_clears_locale(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = authed_client(settings)
    res = client.patch("/api/v1/me", json={"locale": "zh-Hant"})
    assert res.status_code == 200, res.text
    assert res.json()["locale"] == "zh-Hant"

    client.post("/api/v1/auth/logout")
    other = TestClient(create_app(settings))
    login = other.post("/api/v1/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    assert login.json()["locale"] == "zh-Hant"

    assert other.patch("/api/v1/me", json={}).json()["locale"] == "zh-Hant"
    cleared = other.patch("/api/v1/me", json={"locale": None})
    assert cleared.status_code == 200
    assert cleared.json()["locale"] is None


def test_patch_me_rejects_unknown_locale(tmp_path: Path) -> None:
    client = authed_client(_settings(tmp_path))
    res = client.patch("/api/v1/me", json={"locale": "zh-TW"})
    assert res.status_code == 400
    body = res.json()
    assert body["code"] == "invalid_locale"
    assert body["params"] == {"locale": "zh-TW"}
    assert "zh-Hant" in body["detail"]


def test_patch_me_requires_login(tmp_path: Path) -> None:
    client = TestClient(create_app(_settings(tmp_path)))
    res = client.patch("/api/v1/me", json={"locale": "en"})
    assert res.status_code == 401
    assert res.json()["code"] == "not_authenticated"


def test_error_codes_on_auth(tmp_path: Path) -> None:
    client = TestClient(create_app(_settings(tmp_path)))
    short = client.post(
        "/api/v1/auth/setup", json={"username": "admin", "password": "short", "display_name": ""}
    )
    assert short.json() == {
        "detail": "password must be at least 8 characters",
        "code": "password_too_short",
        "params": {"min": 8},
    }
    client.post(
        "/api/v1/auth/setup",
        json={"username": ADMIN_USER, "password": ADMIN_PASS, "display_name": ""},
    )
    client.post("/api/v1/auth/logout")
    wrong = client.post("/api/v1/auth/login", json={"username": ADMIN_USER, "password": "wrongpass"})
    assert wrong.status_code == 401
    assert wrong.json()["code"] == "invalid_credentials"


def test_migration_adds_locale_column(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.data_dir.mkdir(parents=True)
    conn = sqlite3.connect(settings.database_path)
    conn.execute(
        'CREATE TABLE "user" (id VARCHAR(36) PRIMARY KEY, provider VARCHAR(32) NOT NULL, '
        "provider_subject VARCHAR(255) NOT NULL, display_name VARCHAR(255) NOT NULL, "
        "role VARCHAR(32) NOT NULL, rev INTEGER NOT NULL, updated_at DATETIME, deleted_at DATETIME)"
    )
    conn.execute(
        "INSERT INTO \"user\" VALUES ('u1', 'local', 'old', 'Old', 'admin', 1, NULL, NULL)"
    )
    conn.commit()
    conn.close()

    engine = make_engine(settings)
    try:
        with engine.connect() as db:
            cols = {row[1] for row in db.execute(text('PRAGMA table_info("user")'))}
            assert "locale" in cols
            assert db.execute(text('SELECT locale FROM "user"')).scalar() is None
    finally:
        engine.dispose()
