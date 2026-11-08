"""The display time zone: ``user.time_zone`` saved with ``PATCH /me`` (doc/i18n.md § Time zone)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from authutil import ADMIN_PASS, ADMIN_USER, authed_client
from mosaicwave.config import Settings
from mosaicwave.db import make_engine
from mosaicwave.i18n import translate
from mosaicwave.main import create_app
from mosaicwave.timezone import canonical_zone


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)


def test_zone_names_are_checked_against_the_iana_database() -> None:
    assert canonical_zone("Asia/Taipei") == "Asia/Taipei"
    assert canonical_zone("asia/taipei") == "Asia/Taipei"  # the database's own spelling is stored
    assert canonical_zone(" UTC ") == "UTC"
    for bad in ("", None, "Mars/Base", "../etc/passwd", "Asia/", "+08:00", "GMT+8 please"):
        assert canonical_zone(bad) is None, bad


def test_time_zone_defaults_to_automatic(tmp_path: Path) -> None:
    client = authed_client(_settings(tmp_path))
    assert client.get("/api/v1/me").json()["time_zone"] is None
    assert client.get("/api/v1/auth/status").json()["me"]["time_zone"] is None


def test_patch_me_saves_and_clears_the_time_zone(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = authed_client(settings)
    res = client.patch("/api/v1/me", json={"time_zone": "asia/taipei"})
    assert res.status_code == 200, res.text
    assert res.json()["time_zone"] == "Asia/Taipei"

    # It follows the account to another device.
    client.post("/api/v1/auth/logout")
    other = TestClient(create_app(settings))
    login = other.post("/api/v1/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    assert login.json()["time_zone"] == "Asia/Taipei"

    cleared = other.patch("/api/v1/me", json={"time_zone": None})
    assert cleared.status_code == 200
    assert cleared.json()["time_zone"] is None


def test_zone_and_locale_are_separate_settings(tmp_path: Path) -> None:
    client = authed_client(_settings(tmp_path))
    client.patch("/api/v1/me", json={"locale": "zh-Hant", "time_zone": "Asia/Taipei"})
    only_zone = client.patch("/api/v1/me", json={"time_zone": "Europe/Paris"}).json()
    assert (only_zone["locale"], only_zone["time_zone"]) == ("zh-Hant", "Europe/Paris")
    only_locale = client.patch("/api/v1/me", json={"locale": "en"}).json()
    assert (only_locale["locale"], only_locale["time_zone"]) == ("en", "Europe/Paris")
    assert client.patch("/api/v1/me", json={}).json()["time_zone"] == "Europe/Paris"


@pytest.mark.parametrize("bad", ["Mars/Base", "../etc/passwd", "", "GMT+8"])
def test_unknown_zone_is_refused_and_nothing_is_saved(tmp_path: Path, bad: str) -> None:
    client = authed_client(_settings(tmp_path))
    client.patch("/api/v1/me", json={"locale": "zh-Hans", "time_zone": "Asia/Taipei"})
    res = client.patch("/api/v1/me", json={"locale": "en", "time_zone": bad})
    assert res.status_code == 400
    body = res.json()
    assert body["code"] == "invalid_time_zone"
    assert body["params"] == {"time_zone": bad}
    me = client.get("/api/v1/me").json()
    assert (me["locale"], me["time_zone"]) == ("zh-Hans", "Asia/Taipei")  # the locale in the same body was not saved


def test_missing_zone_database_is_a_server_error_not_an_unknown_zone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("mosaicwave.api.v1.auth.zone_data_available", lambda: False)
    client = authed_client(_settings(tmp_path))
    res = client.patch("/api/v1/me", json={"time_zone": "Asia/Taipei"})
    assert res.status_code == 503
    assert res.json()["code"] == "time_zone_data_missing"
    assert "tzdata" in res.json()["detail"]
    assert client.get("/api/v1/me").json()["time_zone"] is None
    # Clearing the zone needs no database.
    assert client.patch("/api/v1/me", json={"time_zone": None}).status_code == 200


def test_zone_error_is_written_in_the_response_language(tmp_path: Path) -> None:
    client = authed_client(_settings(tmp_path))
    res = client.patch(
        "/api/v1/me", json={"time_zone": "Mars/Base"}, headers={"X-MosaicWave-Language": "zh-Hant"}
    )
    assert res.json()["detail"] == translate("invalid_time_zone", {"time_zone": "Mars/Base"}, "zh-Hant", "")
    assert "Mars/Base" in res.json()["detail"]


def test_timestamps_stay_utc_whatever_zone_is_saved(tmp_path: Path) -> None:
    client = authed_client(_settings(tmp_path))
    client.patch("/api/v1/me", json={"time_zone": "Pacific/Kiritimati"})  # UTC+14
    job = client.post("/api/v1/me/backups", json={}).json()
    created = job["created_at"]
    assert created
    # Naive means UTC to every client; a zone offset must never be added.
    assert "+14" not in created and not created.endswith("-10:00")


def test_migration_adds_the_time_zone_column(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.data_dir.mkdir(parents=True)
    conn = sqlite3.connect(settings.database_path)
    conn.execute(
        'CREATE TABLE "user" (id VARCHAR(36) PRIMARY KEY, provider VARCHAR(32) NOT NULL, '
        "provider_subject VARCHAR(255) NOT NULL, display_name VARCHAR(255) NOT NULL, "
        "role VARCHAR(32) NOT NULL, rev INTEGER NOT NULL, updated_at DATETIME, deleted_at DATETIME)"
    )
    conn.execute("INSERT INTO \"user\" VALUES ('u1', 'local', 'old', 'Old', 'admin', 1, NULL, NULL)")
    conn.commit()
    conn.close()

    engine = make_engine(settings)
    try:
        with engine.connect() as db:
            cols = {row[1] for row in db.execute(text('PRAGMA table_info("user")'))}
            assert "time_zone" in cols
            assert db.execute(text('SELECT time_zone FROM "user"')).scalar() is None
    finally:
        engine.dispose()
