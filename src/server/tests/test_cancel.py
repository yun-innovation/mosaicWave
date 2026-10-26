from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from authutil import authed_client, library_dir, scan_library
from test_backup import _make_member, _seed, _settings
from test_takeout import _jpeg_bytes, _write_takeout

from mosaicwave.config import Settings
from mosaicwave.library import importjob
from mosaicwave.models import Job, JobEvent, new_id, utcnow
from sqlalchemy import select


class Gate:
    """Holds a running job at its first (or Nth) step until the test lets it go."""

    def __init__(self, hold_at: int = 1) -> None:
        self.hold_at = hold_at
        self.calls = 0
        self.reached = threading.Event()
        self.release = threading.Event()

    def step(self) -> None:
        self.calls += 1
        if self.calls == self.hold_at:
            self.reached.set()
            assert self.release.wait(10), "test never released the job"


def _until(client: TestClient, job_id: str, statuses: set[str], timeout: float = 15) -> dict:
    deadline = time.monotonic() + timeout
    body: dict = {}
    while time.monotonic() < deadline:
        body = client.get(f"/api/v1/jobs/{job_id}").json()
        if body["status"] in statuses:
            return body
        time.sleep(0.05)
    raise TimeoutError(body)


def _events(client: TestClient, job_id: str) -> list[str]:
    return [e["message"] for e in client.get(f"/api/v1/jobs/{job_id}/events").json()]


def _photos(client: TestClient, settings: Settings, count: int) -> None:
    folder = library_dir(settings, client)
    for n in range(count):
        (folder / f"p{n}.jpg").write_bytes(_jpeg_bytes((10 * n, 40, 200)))
    scan_library(client)


def _gate_thumbs(monkeypatch: pytest.MonkeyPatch, gate: Gate) -> None:
    real = importjob.ensure_thumb

    def gated(*args, **kwargs):  # type: ignore[no-untyped-def]
        gate.step()
        return real(*args, **kwargs)

    monkeypatch.setattr(importjob, "ensure_thumb", gated)


def test_cancel_thumbnails_keeps_what_was_made(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    client = authed_client(settings)
    _photos(client, settings, 3)
    gate = Gate()
    _gate_thumbs(monkeypatch, gate)

    job = client.post("/api/v1/thumbs/generate").json()
    assert gate.reached.wait(10)
    res = client.post(f"/api/v1/jobs/{job['id']}/cancel")
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "running"  # it ends at the next check, not instantly
    assert client.get("/api/v1/jobs/running").json()["job"]["cancelling"] is True
    gate.release.set()

    done = _until(client, job["id"], {"cancelled", "done", "error"})
    assert done["status"] == "cancelled" and done["error"] is None
    assert (done["processed"], done["total"]) == (1, 3)
    assert done["finished_at"]
    log = _events(client, job["id"])
    assert "Cancel requested by owner" in log
    assert any(line.startswith("Cancelled:") for line in log)

    # the server is free again and the same job can simply be run again
    again = client.post("/api/v1/thumbs/generate")
    assert again.status_code == 200, again.text
    assert _until(client, again.json()["id"], {"done", "error"})["status"] == "done"


def test_cancel_a_backup_leaves_no_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    gate = Gate()
    real = importjob.backup_site

    def slow(settings_, out, *, include_thumbs, on_progress, **kwargs):  # type: ignore[no-untyped-def]
        def gated(done: int, total: int, rel: str) -> None:
            gate.step()
            on_progress(done, total, rel)

        return real(settings_, out, include_thumbs=include_thumbs, on_progress=gated, **kwargs)

    monkeypatch.setattr(importjob, "backup_site", slow)
    job = client.post("/api/v1/backups", json={}).json()
    assert gate.reached.wait(10)
    assert client.post(f"/api/v1/jobs/{job['id']}/cancel").status_code == 200
    gate.release.set()

    done = _until(client, job["id"], {"cancelled", "done", "error"})
    assert done["status"] == "cancelled"
    assert list(settings.backup_dir.iterdir()) == []  # no .part and no zip
    assert client.get("/api/v1/backups").json() == []


def test_cancel_an_import_keeps_finished_items_and_can_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    takeout = tmp_path / "Takeout"
    _write_takeout(takeout, _jpeg_bytes())
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=takeout, source_dir=None)
    client = authed_client(settings)
    # Hold the job after the first item is finished (the second progress report).
    gate = Gate(hold_at=2)
    real = importjob.import_takeout

    def slow(session, root, *, on_progress, **kwargs):  # type: ignore[no-untyped-def]
        def gate_then_report(done: int, total: int, rel: str) -> None:
            gate.step()
            on_progress(done, total, rel)

        return real(session, root, on_progress=gate_then_report, **kwargs)

    monkeypatch.setattr(importjob, "import_takeout", slow)
    job = client.post("/api/v1/import/takeout", json={"path": str(takeout)}).json()
    assert gate.reached.wait(10)
    assert client.post(f"/api/v1/jobs/{job['id']}/cancel").status_code == 200
    gate.release.set()

    done = _until(client, job["id"], {"cancelled", "done", "error"})
    assert done["status"] == "cancelled"
    assert len(client.get("/api/v1/assets").json()["items"]) == 1  # the finished item is kept

    # Importing again picks up the rest (same files are skipped by hash).
    gate.release.set()
    rerun = client.post("/api/v1/import/takeout", json={"path": str(takeout)}).json()
    assert _until(client, rerun["id"], {"done", "error"})["status"] == "done"
    assert len(client.get("/api/v1/assets").json()["items"]) == 1
    assert [a["name"] for a in client.get("/api/v1/albums").json()] == ["Trip to Kyoto"]


def _fake_running(client: TestClient, user_id: str) -> str:
    """A job row that says running with no worker behind it, to test who may cancel."""
    session = client.app.state.session_factory()
    job_id = new_id()
    session.add(
        Job(id=job_id, kind="thumb_batch", status="running", created_at=utcnow(), user_id=user_id, result_json="{}")
    )
    session.commit()
    session.close()
    return job_id


def _log(client: TestClient, job_id: str) -> list[str]:
    session = client.app.state.session_factory()
    try:
        rows = session.scalars(select(JobEvent).where(JobEvent.job_id == job_id))
        return [row.message for row in rows]
    finally:
        session.close()


def test_who_may_cancel(tmp_path: Path) -> None:
    admin = authed_client(_settings(tmp_path))
    member = _make_member(admin)
    other = TestClient(admin.app)
    assert admin.put(
        "/api/v1/users",
        json={"username": "lee", "password": "otherpass1", "display_name": "Lee", "role": "member"},
    ).status_code == 200
    assert other.post("/api/v1/auth/login", json={"username": "lee", "password": "otherpass1"}).status_code == 200
    member_id = member.get("/api/v1/me").json()["id"]
    job_id = _fake_running(admin, member_id)

    assert other.post(f"/api/v1/jobs/{job_id}/cancel").status_code == 404  # not theirs, not an admin
    assert member.post("/api/v1/jobs/nope/cancel").status_code == 404
    assert _log(admin, job_id) == []

    assert admin.post(f"/api/v1/jobs/{job_id}/cancel").status_code == 200
    assert _log(admin, job_id) == ["Cancel requested by admin"]
    # Asking again changes nothing and is not an error.
    assert member.post(f"/api/v1/jobs/{job_id}/cancel").status_code == 200
    assert _log(admin, job_id) == ["Cancel requested by admin"]

    session = admin.app.state.session_factory()
    row = session.get(Job, job_id)
    row.status = "done"
    session.commit()
    session.close()
    ended = member.post(f"/api/v1/jobs/{job_id}/cancel")
    assert ended.status_code == 409 and ended.json()["code"] == "job_not_running"


def test_admin_ends_maintenance_wait_by_cancelling_the_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    admin = authed_client(settings)
    member = _make_member(admin)
    _photos(member, settings, 2)
    gate = Gate()
    _gate_thumbs(monkeypatch, gate)

    job = member.post("/api/v1/thumbs/generate").json()
    assert gate.reached.wait(10)
    assert admin.put("/api/v1/maintenance", json={"enabled": True, "reason": "Backup"}).json()["state"] == "draining"
    running = admin.get("/api/v1/jobs/running").json()["job"]
    assert running["id"] == job["id"] and running["owner"] == "Pat" and running["cancelling"] is False
    assert member.post(f"/api/v1/jobs/{job['id']}/cancel").status_code == 503  # locked out like all else

    assert admin.post(f"/api/v1/jobs/{running['id']}/cancel").status_code == 200
    assert admin.get("/api/v1/jobs/running").json()["job"]["cancelling"] is True
    gate.release.set()

    deadline = time.monotonic() + 15
    while admin.get("/api/v1/maintenance").json()["state"] != "on":
        assert time.monotonic() < deadline, "maintenance never reached on"
        time.sleep(0.05)
    assert admin.get("/api/v1/jobs/running").json() == {"job": None}
    assert admin.post("/api/v1/backups", json={}).status_code == 200  # the way is free for a backup
