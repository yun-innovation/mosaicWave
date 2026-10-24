from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from authutil import ADMIN_PASS, ADMIN_USER, authed_client

from mosaicwave import maintenance
from mosaicwave.config import Settings
from mosaicwave.maintenance import main as maintenance_cli
from mosaicwave.models import Job, new_id, utcnow


def _settings(root: Path) -> Settings:
    return Settings(data_dir=root / "data", takeout_dir=None, source_dir=None)


def _member(admin: TestClient) -> TestClient:
    res = admin.put(
        "/api/v1/users",
        json={"username": "pat", "password": "memberpass", "display_name": "Pat", "role": "member"},
    )
    assert res.status_code == 200, res.text
    member = TestClient(admin.app)
    login = member.post("/api/v1/auth/login", json={"username": "pat", "password": "memberpass"})
    assert login.status_code == 200, login.text
    return member


def _switch(client: TestClient, enabled: bool, reason: str = "") -> dict:
    res = client.put("/api/v1/maintenance", json={"enabled": enabled, "reason": reason})
    assert res.status_code == 200, res.text
    return res.json()


def test_off_by_default_and_status_is_public(tmp_path: Path) -> None:
    admin = authed_client(_settings(tmp_path))
    anon = TestClient(admin.app)
    assert anon.get("/api/v1/maintenance").json() == {"state": "off", "reason": "", "since": None}
    assert anon.put("/api/v1/maintenance", json={"enabled": True}).status_code == 401


def test_switch_blocks_everyone_but_admins(tmp_path: Path) -> None:
    admin = authed_client(_settings(tmp_path))
    member = _member(admin)
    anon = TestClient(admin.app)
    assert member.get("/api/v1/albums").status_code == 200

    status = _switch(admin, True, "Restoring the library")
    assert status["state"] == "on" and status["reason"] == "Restoring the library" and status["since"]

    for who in (member, anon):
        res = who.get("/api/v1/albums")
        assert res.status_code == 503, res.text
        body = res.json()
        assert body["code"] == "maintenance"
        assert body["params"]["reason"] == "Restoring the library"
        assert res.headers["retry-after"] == "10"
    assert admin.get("/api/v1/albums").status_code == 200
    assert admin.get("/api/v1/backups").status_code == 200

    # Everyone can still see why, and the web page itself keeps loading.
    assert anon.get("/api/v1/maintenance").json()["reason"] == "Restoring the library"
    assert anon.get("/api/v1/health").status_code == 200
    assert anon.get("/api/v1/auth/status").status_code == 200
    assert member.put("/api/v1/maintenance", json={"enabled": False}).status_code == 403  # admin only

    assert _switch(admin, False)["state"] == "off"
    assert member.get("/api/v1/albums").status_code == 200


def test_sign_in_is_admin_only_during_maintenance(tmp_path: Path) -> None:
    admin = authed_client(_settings(tmp_path))
    _member(admin)
    _switch(admin, True, "Backing up")

    fresh = TestClient(admin.app)
    refused = fresh.post("/api/v1/auth/login", json={"username": "pat", "password": "memberpass"})
    assert refused.status_code == 503 and refused.json()["code"] == "maintenance"
    assert fresh.get("/api/v1/me").status_code == 503
    wrong = fresh.post("/api/v1/auth/login", json={"username": "pat", "password": "nope-nope"})
    assert wrong.status_code == 401  # a bad password is still just a bad password

    again = TestClient(admin.app)
    ok = again.post("/api/v1/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    assert ok.status_code == 200
    assert again.get("/api/v1/me").status_code == 200


def test_draining_while_a_job_runs_then_on(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    admin = authed_client(settings)
    session = admin.app.state.session_factory()
    job_id = new_id()
    session.add(Job(id=job_id, kind="thumb_batch", status="running", created_at=utcnow(), result_json="{}"))
    session.commit()

    assert _switch(admin, True, "Soon")["state"] == "draining"
    blocked = admin.post("/api/v1/backups", json={})
    assert blocked.status_code == 409 and blocked.json()["code"] == "job_running"

    job = session.get(Job, job_id)
    job.status = "done"
    session.commit()
    session.close()
    assert admin.get("/api/v1/maintenance").json()["state"] == "on"
    assert admin.post("/api/v1/backups", json={}).status_code == 200


def test_reason_is_trimmed_and_since_survives_a_change(tmp_path: Path) -> None:
    admin = authed_client(_settings(tmp_path))
    first = _switch(admin, True, "  one  ")
    assert first["reason"] == "one"
    second = _switch(admin, True, "two")
    assert second["reason"] == "two" and second["since"] == first["since"]
    too_long = admin.put("/api/v1/maintenance", json={"enabled": True, "reason": "x" * 501})
    assert too_long.status_code == 422


def test_flag_written_by_another_process_takes_effect_at_once(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    admin = authed_client(settings)
    member = _member(admin)
    maintenance.enable(settings, "from the command line", by="cli")
    assert member.get("/api/v1/albums").status_code == 503
    maintenance.disable(settings)
    assert member.get("/api/v1/albums").status_code == 200


def test_unreadable_flag_still_means_maintenance(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    admin = authed_client(settings)
    member = _member(admin)
    maintenance.flag_path(settings).write_text("{ not json", encoding="utf-8")
    assert member.get("/api/v1/albums").status_code == 503
    assert admin.get("/api/v1/maintenance").json()["state"] == "on"


def test_start_in_maintenance_from_the_environment(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert maintenance.start_from_env(settings, {}) is False
    assert maintenance.start_from_env(settings, {"MOSAICWAVE_MAINTENANCE": "0"}) is False
    assert maintenance.read(settings) is None
    assert maintenance.start_from_env(settings, {"MOSAICWAVE_MAINTENANCE": "1"}) is True
    flag = maintenance.read(settings)
    assert flag is not None and flag.reason == maintenance.DEFAULT_START_REASON
    # a reason an admin set earlier is not overwritten by a restart
    maintenance.enable(settings, "kept", by="cli")
    assert maintenance.start_from_env(settings, {"MOSAICWAVE_MAINTENANCE": "1"}) is False
    assert maintenance.read(settings).reason == "kept"


def test_api_started_with_the_variable_is_in_maintenance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mosaicwave.main import create_app

    monkeypatch.setenv("MOSAICWAVE_MAINTENANCE", "1")
    app = create_app(_settings(tmp_path))
    client = TestClient(app)
    assert client.get("/api/v1/maintenance").json()["state"] == "on"


def test_cli_on_off_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setenv("MOSAICWAVE_DATA_DIR", str(tmp_path / "data"))
    settings = Settings.from_env()
    assert maintenance_cli(["status"]) == 0
    assert "off" in capsys.readouterr().out

    assert maintenance_cli(["on", "--reason", "Moving the NAS"]) == 0
    out = capsys.readouterr().out
    assert "is on" in out and "Moving the NAS" in out
    flag = maintenance.read(settings)
    assert flag is not None and flag.by == "cli" and flag.reason == "Moving the NAS"

    assert maintenance_cli(["off"]) == 0
    assert "off" in capsys.readouterr().out
    assert maintenance.read(settings) is None
    assert maintenance_cli(["off"]) == 0  # already off is fine


def test_cli_reports_draining_while_a_job_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("MOSAICWAVE_DATA_DIR", str(tmp_path / "data"))
    admin = authed_client(Settings.from_env())
    session = admin.app.state.session_factory()
    session.add(Job(id=new_id(), kind="thumb_batch", status="running", created_at=utcnow(), result_json="{}"))
    session.commit()
    session.close()
    assert maintenance_cli(["on"]) == 0
    assert "draining" in capsys.readouterr().out
