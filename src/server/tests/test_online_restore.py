"""Online site restore: stage while the API runs, swap in at the next start (doc/backup.md)."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from authutil import authed_client, library_dir, scan_library
from test_backup import _dispose, _fake_archive, _make_member, _seed, _settings
from test_cancel import Gate, _until
from test_takeout import _jpeg_bytes

from mosaicwave import restore
from mosaicwave.backup import backup_site
from mosaicwave.config import Settings
from mosaicwave.library import importjob


def _lock(client: TestClient) -> None:
    res = client.put("/api/v1/maintenance", json={"enabled": True, "reason": "restoring"})
    assert res.status_code == 200, res.text


def _stage(client: TestClient, name: str) -> dict:
    res = client.post(f"/api/v1/backups/{name}/restore")
    assert res.status_code == 200, res.text
    return _until(client, res.json()["id"], {"done", "error", "cancelled"})


def _assets(client: TestClient) -> int:
    return len(client.get("/api/v1/assets").json()["items"])


def _archive_then_add_a_photo(settings: Settings, client: TestClient) -> str:
    name = backup_site(settings, settings.backup_dir).path.name
    (library_dir(settings, client) / "late.jpg").write_bytes(_jpeg_bytes((10, 200, 10)))
    scan_library(client)
    assert _assets(client) == 3
    return name


def _restart(settings: Settings, client: TestClient) -> TestClient:
    """What the supervisor does after Apply: a new process starts and runs the swap."""
    _dispose(client)
    client.close()
    return authed_client(settings)


def _until_gone(settings: Settings) -> None:
    """The replaced data is deleted by a background thread right after the swap."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        left = [p for folder in {settings.database_path.parent, settings.data_dir} for p in folder.glob("*.discard-*")]
        if not left:
            return
        time.sleep(0.05)
    raise AssertionError(left)


def _stages(settings: Settings) -> list[Path]:
    found = set(settings.database_path.parent.glob(".restore-staging-*"))
    return sorted(found | set(settings.data_dir.glob(".restore-staging-*")))


def test_stage_then_apply_swaps_at_the_next_start(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    name = _archive_then_add_a_photo(settings, client)
    _lock(client)

    job = _stage(client, name)
    assert job["status"] == "done", job
    assert (job["processed"], job["total"]) == (job["total"], job["total"]) and job["total"] > 0

    state = client.get("/api/v1/backups/restore").json()
    assert state["result"] is None
    assert state["pending"]["archive"] == name
    assert (state["pending"]["summary"]["assets"], state["pending"]["summary"]["users"]) == (2, 1)
    assert _assets(client) == 3  # nothing live changed yet

    applied = client.post("/api/v1/backups/restore/apply")
    assert applied.status_code == 200 and applied.json() == {"restarting": False}  # no restart under pytest

    fresh = _restart(settings, client)
    assert _assets(fresh) == 2
    assert not (library_dir(settings, fresh) / "late.jpg").exists()
    state = fresh.get("/api/v1/backups/restore").json()
    assert state["pending"] is None
    result = state["result"]
    assert result["status"] == "done" and result["archive"] == name and result["message"] is None
    assert result["kept"] == []  # the replaced data is deleted once the new data is in place
    _until_gone(settings)
    assert result["thumbs_missing"] is True
    assert fresh.get("/api/v1/maintenance").json()["state"] == "on"  # the admin turns it off
    assert not _stages(settings) and not restore.pending_path(settings).exists()

    assert fresh.delete("/api/v1/backups/restore").json() == {"pending": None, "result": None}
    assert not restore.result_path(settings).exists()


def test_needs_maintenance_and_something_staged(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    name = backup_site(settings, settings.backup_dir).path.name

    refused = client.post(f"/api/v1/backups/{name}/restore")
    assert refused.status_code == 409 and refused.json()["code"] == "maintenance_required"
    nothing = client.post("/api/v1/backups/restore/apply")
    assert nothing.status_code == 409 and nothing.json()["code"] == "restore_not_staged"

    _lock(client)
    assert _stage(client, name)["status"] == "done"
    client.put("/api/v1/maintenance", json={"enabled": False})
    off = client.post("/api/v1/backups/restore/apply")
    assert off.status_code == 409 and off.json()["code"] == "maintenance_required"

    missing = client.post("/api/v1/backups/mosaicwave-site-20200101-000000.zip/restore")
    assert missing.status_code == 404 and missing.json()["code"] == "backup_not_found"


def test_admin_only(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    admin = _seed(settings)
    name = backup_site(settings, settings.backup_dir).path.name
    member = _make_member(admin)
    assert member.post(f"/api/v1/backups/{name}/restore").status_code == 403
    assert member.get("/api/v1/backups/restore").status_code == 403
    assert member.post("/api/v1/backups/restore/apply").status_code == 403
    assert member.delete("/api/v1/backups/restore").status_code == 403


def test_a_staged_restore_blocks_new_jobs_until_discarded(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    name = backup_site(settings, settings.backup_dir).path.name
    _lock(client)
    assert _stage(client, name)["status"] == "done"
    assert len(_stages(settings)) == 2

    for url in ("/api/v1/backups", f"/api/v1/backups/{name}/restore", "/api/v1/thumbs/generate"):
        res = client.post(url)
        assert res.status_code == 409 and res.json()["code"] == "restore_pending", (url, res.text)

    assert client.delete("/api/v1/backups/restore").json()["pending"] is None
    assert not _stages(settings) and not restore.pending_path(settings).exists()
    again = client.post("/api/v1/backups")
    assert again.status_code == 200, again.text
    assert _until(client, again.json()["id"], {"done", "error"})["status"] == "done"


def test_cancel_while_staging_leaves_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    name = backup_site(settings, settings.backup_dir).path.name
    _lock(client)

    gate = Gate()
    real = importjob.stage_site

    def gated(archive, settings, *, on_progress=None):  # type: ignore[no-untyped-def]
        def step(done: int, total: int, rel: str) -> None:
            gate.step()
            on_progress(done, total, rel)

        return real(archive, settings, on_progress=step)

    monkeypatch.setattr(importjob, "stage_site", gated)
    job = client.post(f"/api/v1/backups/{name}/restore").json()
    assert gate.reached.wait(10)
    assert client.post(f"/api/v1/jobs/{job['id']}/cancel").status_code == 200
    gate.release.set()

    done = _until(client, job["id"], {"cancelled", "done", "error"})
    assert done["status"] == "cancelled", done
    assert not _stages(settings) and not restore.pending_path(settings).exists()
    assert client.get("/api/v1/backups/restore").json() == {"pending": None, "result": None}
    assert _assets(client) == 2


def test_a_damaged_archive_fails_the_job_and_stages_nothing(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    bad = _fake_archive(settings.backup_dir / "mosaicwave-site-20200101-000000.zip", db=b"not a database")
    _lock(client)

    job = _stage(client, bad.name)
    assert job["status"] == "error" and job["error"]
    assert not _stages(settings) and not restore.pending_path(settings).exists()
    assert _assets(client) == 2
    # the failure left nothing behind, so the next job can start
    assert client.post("/api/v1/backups").status_code == 200


def test_a_failed_swap_keeps_the_old_data_and_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    name = _archive_then_add_a_photo(settings, client)
    _lock(client)
    assert _stage(client, name)["status"] == "done"

    def boom(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise OSError("disk went away")

    monkeypatch.setattr(restore, "_swap", boom)
    fresh = _restart(settings, client)
    monkeypatch.undo()

    assert _assets(fresh) == 3  # the live data was never touched
    result = fresh.get("/api/v1/backups/restore").json()["result"]
    assert result["status"] == "error" and "disk went away" in result["message"]
    assert not _stages(settings) and not restore.pending_path(settings).exists()


def test_missing_staged_files_are_reported_not_applied(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    name = _archive_then_add_a_photo(settings, client)
    _lock(client)
    assert _stage(client, name)["status"] == "done"
    for stage in _stages(settings):
        shutil.rmtree(stage)

    fresh = _restart(settings, client)
    assert _assets(fresh) == 3
    result = fresh.get("/api/v1/backups/restore").json()["result"]
    assert result["status"] == "error" and "missing" in result["message"]
    assert not restore.pending_path(settings).exists()


def test_start_clears_leftover_staging_folders(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    leftover = settings.data_dir / ".restore-staging-data-20200101-000000"
    leftover.mkdir()
    (leftover / "x").write_text("x")
    fresh = _restart(settings, client)
    assert not leftover.exists()
    assert fresh.get("/api/v1/backups/restore").json() == {"pending": None, "result": None}


# --- choosing the archive: on the server (any path) or on this device (upload) -------------------


def _archive_elsewhere(settings: Settings, folder: Path) -> Path:
    return backup_site(settings, folder).path


def test_restore_from_a_path_on_the_server(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    elsewhere = _archive_elsewhere(settings, tmp_path / "usb")
    (library_dir(settings, client) / "late.jpg").write_bytes(_jpeg_bytes((10, 200, 10)))
    scan_library(client)
    assert _assets(client) == 3

    blank = client.post("/api/v1/backups/restore", json={"path": "  "})
    assert blank.status_code == 400 and blank.json()["code"] == "backup_path_required"
    locked_out = client.post("/api/v1/backups/restore", json={"path": str(elsewhere)})
    assert locked_out.status_code == 409 and locked_out.json()["code"] == "maintenance_required"

    _lock(client)
    folder = client.post("/api/v1/backups/restore", json={"path": str(tmp_path)})
    assert folder.status_code == 400 and folder.json()["code"] == "backup_not_a_file"
    junk = tmp_path / "notes.zip"
    junk.write_bytes(b"not a zip at all")
    bad = client.post("/api/v1/backups/restore", json={"path": str(junk)})
    assert bad.status_code == 400 and bad.json()["code"] == "backup_invalid"
    assert not [j for j in client.get("/api/v1/jobs", params={"kind": "site_restore"}).json()["items"]]

    res = client.post("/api/v1/backups/restore", json={"path": str(elsewhere)})
    assert res.status_code == 200, res.text
    assert _until(client, res.json()["id"], {"done", "error", "cancelled"})["status"] == "done"
    assert elsewhere.exists()  # restored from where it is; nothing was copied or moved
    assert not (settings.backup_dir / elsewhere.name).exists()
    assert client.get("/api/v1/backups/restore").json()["pending"]["archive"] == elsewhere.name

    assert client.post("/api/v1/backups/restore/apply").status_code == 200
    fresh = _restart(settings, client)
    assert _assets(fresh) == 2
    assert fresh.get("/api/v1/backups/restore").json()["result"]["status"] == "done"


def test_server_path_restore_is_admin_only(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    admin = _seed(settings)
    archive = _archive_elsewhere(settings, tmp_path / "usb")
    member = _make_member(admin)
    assert member.post("/api/v1/backups/restore", json={"path": str(archive)}).status_code == 403
    assert member.put("/api/v1/backups/upload", content=b"x").status_code == 403


def test_upload_from_the_device_then_restore(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    archive = _archive_elsewhere(settings, tmp_path / "laptop")
    data = archive.read_bytes()
    (library_dir(settings, client) / "late.jpg").write_bytes(_jpeg_bytes((10, 200, 10)))
    scan_library(client)

    up = client.put("/api/v1/backups/upload", content=data)
    assert up.status_code == 200, up.text
    info = up.json()
    assert info["name"] == archive.name and info["assets"] == 2 and info["size"] == len(data)
    assert [b["name"] for b in client.get("/api/v1/backups").json()] == [archive.name]
    assert not list(settings.backup_dir.glob(".upload-*"))

    again = client.put("/api/v1/backups/upload", content=data)  # the same file twice is one archive
    assert again.status_code == 200 and again.json()["name"] == archive.name
    assert len(client.get("/api/v1/backups").json()) == 1

    _lock(client)
    assert _stage(client, info["name"])["status"] == "done"
    assert client.post("/api/v1/backups/restore/apply").status_code == 200
    fresh = _restart(settings, client)
    assert _assets(fresh) == 2


def test_uploaded_archive_is_named_from_its_own_time(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    when = {"created_at": "2026-03-04T05:06:07+00:00"}
    first = _fake_archive(tmp_path / "my backup (1).zip", db=b"x", manifest=when).read_bytes()
    second = _fake_archive(tmp_path / "other.zip", db=b"xxxxxxxx", manifest=when).read_bytes()

    one = client.put("/api/v1/backups/upload", content=first).json()
    two = client.put("/api/v1/backups/upload", content=second).json()
    assert one["name"] == "mosaicwave-site-20260304-050607.zip"
    assert two["name"] == "mosaicwave-site-20260304-050608.zip"  # a different file never overwrites


def test_bad_uploads_are_refused_and_leave_nothing(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    too_new = _fake_archive(tmp_path / "new.zip", db=b"x", manifest={"schema_version": 99}).read_bytes()
    for content in (b"this is not a zip", too_new, b""):
        res = client.put("/api/v1/backups/upload", content=content)
        assert res.status_code == 400 and res.json()["code"] == "backup_invalid", res.text
    assert client.get("/api/v1/backups").json() == []
    assert not list(settings.backup_dir.glob(".upload-*")) and not list(settings.backup_dir.glob("*.zip"))


def test_upload_is_refused_while_a_restore_is_staged(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    name = backup_site(settings, settings.backup_dir).path.name
    _lock(client)
    assert _stage(client, name)["status"] == "done"
    res = client.put("/api/v1/backups/upload", content=b"x")
    assert res.status_code == 409 and res.json()["code"] == "restore_pending"


def test_folder_picker_lists_zips_for_backups_only_for_admins(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    admin = _seed(settings)
    folder = tmp_path / "share"
    folder.mkdir()
    for name in ("renamed.ZIP", "notes.txt", "takeout-20260101T000000Z-001.zip"):
        (folder / name).write_bytes(b"x")

    def files(**params: str) -> list[str]:
        res = admin.get("/api/v1/fs/list", params={"path": str(folder), **params})
        assert res.status_code == 200, res.text
        return sorted(e["name"] for e in res.json()["entries"] if not e["is_dir"])

    assert files() == ["takeout-20260101T000000Z-001.zip"]  # Import is unchanged
    assert files(kind="backup") == ["renamed.ZIP", "takeout-20260101T000000Z-001.zip"]
    assert admin.get("/api/v1/fs/list", params={"kind": "other"}).status_code == 422
    member = _make_member(admin)
    assert member.get("/api/v1/fs/list", params={"path": str(folder), "kind": "backup"}).status_code == 403
    assert member.get("/api/v1/fs/list", params={"path": str(folder)}).status_code == 200
