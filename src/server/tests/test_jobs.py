from __future__ import annotations

from pathlib import Path

from mosaicwave.config import Settings
from mosaicwave.library.importjob import INTERRUPTED, KIND_TAKEOUT
from mosaicwave.models import Job, new_id, utcnow
from test_takeout import _jpeg_bytes, _wait_import, _write_takeout
from authutil import authed_client


def test_takeout_job_survives_app_restart(tmp_path: Path) -> None:
    takeout = tmp_path / "Takeout"
    _write_takeout(takeout, _jpeg_bytes())
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    client = authed_client(settings)
    body = _wait_import(client, str(takeout))
    job_id = body["id"]
    client2 = authed_client(settings)
    listed = client2.get("/api/v1/jobs").json()["items"]
    assert listed[0]["id"] == job_id
    assert listed[0]["status"] == "done"
    assert listed[0]["assets"] == 1
    one = client2.get(f"/api/v1/jobs/{job_id}")
    assert one.status_code == 200
    assert one.json()["status"] == "done"


def test_interrupted_running_job_marked_error(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    client = authed_client(settings)
    user_id = client.get("/api/v1/me").json()["id"]
    session = client.app.state.session_factory()
    try:
        session.add(
            Job(
                id=new_id(),
                kind=KIND_TAKEOUT,
                status="running",
                created_at=utcnow(),
                started_at=utcnow(),
                user_id=user_id,
                input_json='{"path": "D:\\\\gone"}',
            )
        )
        session.commit()
    finally:
        session.close()
    client2 = authed_client(settings)
    items = client2.get("/api/v1/jobs").json()["items"]
    assert items[0]["status"] == "error"
    assert items[0]["error"] == INTERRUPTED
