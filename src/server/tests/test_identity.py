"""Server identity: server_id, data_epoch and instance_id (doc/mobile.md § Server identity)."""

from __future__ import annotations

import sqlite3
import time
import uuid
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from mosaicwave import identity, schema
from mosaicwave.backup import backup_site
from mosaicwave.config import Settings
from mosaicwave.db import make_engine
from mosaicwave.main import create_app
from mosaicwave.restore import apply_pending, restore_site, stage_site, write_pending
from mosaicwave.schema.steps import STEPS
from tests.authutil import authed_client


def _settings(root: Path) -> Settings:
    return Settings(data_dir=root / "data", takeout_dir=None, source_dir=None)


def _status(client: TestClient) -> dict:
    res = client.get("/api/v1/auth/status")
    assert res.status_code == 200, res.text
    return res.json()


def _dispose(client: TestClient) -> None:
    client.app.state.library_dbs.dispose_all()
    client.app.state.engine.dispose()


def _stored(settings: Settings) -> tuple[str | None, int]:
    con = sqlite3.connect(settings.database_path)
    try:
        return identity.read_raw(con)
    finally:
        con.close()


def identity_of(settings: Settings) -> str | None:
    return _stored(settings)[0]


def test_new_server_has_ids_and_status_reports_them(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = authed_client(settings)
    status = _status(client)
    assert str(uuid.UUID(status["server_id"])) == status["server_id"]
    assert str(uuid.UUID(status["instance_id"])) == status["instance_id"]
    assert status["data_epoch"] == 1
    assert status["server_id"] != status["instance_id"]
    assert _status(client) == status | {"me": status["me"]}
    # Public: the same answer without signing in.
    anonymous = TestClient(client.app)
    seen = _status(anonymous)
    assert (seen["server_id"], seen["instance_id"], seen["data_epoch"]) == (
        status["server_id"],
        status["instance_id"],
        1,
    )


def test_instance_id_lives_in_a_file_and_survives_restart(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = authed_client(settings)
    instance = _status(first)["instance_id"]
    boot = Path(settings.bootstrap_dir or settings.data_dir)
    assert (boot / identity.INSTANCE_FILE).read_text(encoding="utf-8").strip() == instance
    _dispose(first)
    second = TestClient(create_app(settings))
    again = _status(second)
    assert again["instance_id"] == instance
    assert again["server_id"] == identity_of(settings)


def test_upgrade_adds_ids_and_keeps_them_afterwards(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = settings.database_path
    db.parent.mkdir(parents=True)
    con = sqlite3.connect(db)
    try:
        for step in STEPS[:7]:  # a v7 database: site_setting exists, holds a setting, has no identity
            step.apply(con)
        con.execute("PRAGMA user_version = 7")
        con.execute(
            "INSERT INTO site_setting (key, value, updated_at) VALUES ('session_days_app', '30', '2026-01-01')"
        )
        con.commit()
    finally:
        con.close()
    assert _stored(settings) == (None, 0)

    make_engine(settings).dispose()
    server_id, epoch = _stored(settings)
    assert server_id is not None and str(uuid.UUID(server_id)) == server_id
    assert epoch == 1
    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT value FROM site_setting WHERE key='session_days_app'").fetchone() == ("30",)
        assert con.execute("PRAGMA user_version").fetchone() == (schema.CURRENT_VERSION,)
    finally:
        con.close()

    make_engine(settings).dispose()  # a second start changes nothing
    assert _stored(settings) == (server_id, 1)


def test_site_archive_carries_the_ids_and_never_the_instance(tmp_path: Path) -> None:
    settings = _settings(tmp_path / "a")
    client = authed_client(settings)
    status = _status(client)
    archive = backup_site(settings, tmp_path / "out").path
    with zipfile.ZipFile(archive) as zf:
        assert not [n for n in zf.namelist() if n.endswith(identity.INSTANCE_FILE)]
        zf.extract("library.db", tmp_path / "unzipped")
    con = sqlite3.connect(tmp_path / "unzipped" / "library.db")
    try:
        assert identity.read_raw(con) == (status["server_id"], 1)
    finally:
        con.close()


def test_restore_gives_a_new_id_by_default_and_raises_the_epoch(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = authed_client(settings)
    before = _status(client)
    archive = backup_site(settings, tmp_path / "out").path
    _dispose(client)

    restore_site(archive, settings)
    assert _stored(settings)[0] != before["server_id"]
    assert _stored(settings)[1] == before["data_epoch"] + 1
    after = _status(authed_client(settings))
    assert after["server_id"] != before["server_id"]
    assert after["data_epoch"] == before["data_epoch"] + 1


def test_restore_that_replaces_the_original_keeps_the_id(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = authed_client(settings)
    before = _status(client)
    archive = backup_site(settings, tmp_path / "out").path
    _dispose(client)

    restore_site(archive, settings, keep_site_id=True)
    after = _status(authed_client(settings))
    assert after["server_id"] == before["server_id"]
    assert after["data_epoch"] == before["data_epoch"] + 1


def test_restore_onto_new_hardware_keeps_the_id_and_the_epoch_rises(tmp_path: Path) -> None:
    source = _settings(tmp_path / "a")
    client = authed_client(source)
    before = _status(client)
    archive = backup_site(source, tmp_path / "out").path

    fresh = _settings(tmp_path / "b")  # nothing there: no live epoch to compare with
    restore_site(archive, fresh, keep_site_id=True)
    assert _stored(fresh) == (before["server_id"], before["data_epoch"] + 1)
    kept = _status(authed_client(fresh))
    assert kept["server_id"] == before["server_id"]
    assert kept["data_epoch"] == before["data_epoch"] + 1


def test_epoch_goes_above_the_live_servers_epoch(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = authed_client(settings)
    archive = backup_site(settings, tmp_path / "out").path
    client.post("/api/v1/site/id/reset")  # the live server moves on: epoch 2
    client.post("/api/v1/site/id/reset")  # epoch 3
    live = _status(client)["data_epoch"]
    assert live == 3
    _dispose(client)

    restore_site(archive, settings, keep_site_id=True)
    assert _stored(settings)[1] == live + 1


def test_online_restore_remembers_the_choice_until_start(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = authed_client(settings)
    before = _status(client)
    archive = backup_site(settings, tmp_path / "out").path
    _dispose(client)

    staged = stage_site(archive, settings)
    staged.keep_site_id = True
    write_pending(settings, staged, archive.name)
    assert apply_pending(settings) == "done"
    assert _stored(settings) == (before["server_id"], before["data_epoch"] + 1)

    staged = stage_site(archive, settings)  # the default again: a new id
    write_pending(settings, staged, archive.name)
    assert apply_pending(settings) == "done"
    assert _stored(settings)[0] != before["server_id"]


def test_restore_choice_is_offered_by_the_api(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = authed_client(settings)
    archive = backup_site(settings, tmp_path / "out").path
    on = client.put("/api/v1/maintenance", json={"enabled": True, "reason": "restoring"})
    assert on.status_code == 200, on.text
    posted = client.post("/api/v1/backups/restore", json={"path": str(archive), "keep_site_id": True})
    assert posted.status_code == 200, posted.text
    deadline = time.monotonic() + 20
    state: dict = {}
    while time.monotonic() < deadline:
        state = client.get("/api/v1/backups/restore").json()
        if state["pending"]:
            break
        time.sleep(0.05)
    assert state["pending"]["keep_site_id"] is True


def test_admin_can_make_this_a_new_server(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = authed_client(settings)
    before = _status(client)
    res = client.post("/api/v1/site/id/reset")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["server_id"] != before["server_id"]
    assert body["data_epoch"] == before["data_epoch"] + 1
    after = _status(client)
    assert (after["server_id"], after["data_epoch"]) == (body["server_id"], body["data_epoch"])
    assert after["instance_id"] == before["instance_id"]
    shown = client.get("/api/v1/settings").json()
    assert (shown["server_id"], shown["data_epoch"]) == (body["server_id"], body["data_epoch"])


def test_reset_needs_an_admin(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = authed_client(settings)
    anonymous = TestClient(client.app)
    assert anonymous.post("/api/v1/site/id/reset").status_code in (401, 403)
    assert _status(client)["data_epoch"] == 1
