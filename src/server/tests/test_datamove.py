"""Protected Move library: job, progress, cancel, verification and crash recovery (milestone 13)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from authutil import authed_client, library_dir, scan_library
from test_cancel import Gate, _events, _until
from test_settings import _app_folder_settings, _jpeg_bytes, _maintenance_on, _move

from mosaicwave import config as config_mod
from mosaicwave import datamove
from mosaicwave.config import Settings, read_data_dir_config
from mosaicwave.datamove import COPYING, REWRITING, journal_path, read_journal, recover_data_dir_move, write_journal


def _library(tmp_path: Path) -> tuple[Settings, TestClient, Path]:
    """An admin in maintenance mode with a library in the app folder, a site backup and an export."""
    settings = _app_folder_settings(tmp_path)
    client = authed_client(settings)
    _maintenance_on(client)
    lib = library_dir(settings, client)
    (lib / "one.jpg").write_bytes(_jpeg_bytes())
    scan_library(client)
    boot = settings.bootstrap_dir
    (boot / "backups").mkdir(exist_ok=True)
    (boot / "backups" / "site.zip").write_bytes(b"b" * 3000)
    (boot / "exports" / "u1").mkdir(parents=True, exist_ok=True)
    (boot / "exports" / "u1" / "e.zip").write_bytes(b"e" * 2000)
    return settings, client, lib


def _start(client: TestClient, dest: Path) -> dict:
    res = client.put("/api/v1/settings", json={"data_dir": str(dest)})
    assert res.status_code == 200, res.text
    return res.json()


def _hold_copy(monkeypatch: pytest.MonkeyPatch, gate: Gate) -> None:
    original = config_mod.CopyProgress.file_done

    def slow(self: config_mod.CopyProgress) -> None:
        gate.step()
        original(self)

    monkeypatch.setattr(config_mod.CopyProgress, "file_done", slow)


def test_move_reports_progress_without_file_names_and_leaves_nothing_behind(tmp_path: Path) -> None:
    settings, client, lib = _library(tmp_path)
    boot = settings.bootstrap_dir
    dest = tmp_path / "newhome"
    job = _move(client, {"data_dir": str(dest)})
    assert job["status"] == "done", job
    assert job["total"] >= 1 and job["processed"] == job["total"]
    assert job["current"] is None
    text = json.dumps(job) + " ".join(_events(client, job["id"]))
    for name in ("one.jpg", "site.zip", "e.zip", str(dest), str(boot)):
        assert name not in text
    # The old copies are gone, the journal is gone, and the new folder holds everything.
    assert not journal_path(settings).exists()
    for name in ("libraries", "backups", "exports"):
        assert not (boot / name).exists(), name
        assert (dest / name).is_dir(), name
    assert (boot / "library.db").is_file() and (boot / "accounts").is_dir()
    assert read_data_dir_config(boot) == dest.resolve()
    assert client.app.state.moving is False
    assert len(client.get("/api/v1/assets").json()["items"]) == 1


def test_journal_exists_while_copying_and_library_requests_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, client, _lib = _library(tmp_path)
    gate = Gate()
    _hold_copy(monkeypatch, gate)
    dest = tmp_path / "newhome"
    job = _start(client, dest)
    assert gate.reached.wait(10)
    try:
        journal = read_journal(settings)
        assert journal is not None and journal.phase == COPYING
        assert journal.dest == dest.resolve() and {"libraries", "backups", "exports"} <= set(journal.names)
        busy = client.get("/api/v1/assets")
        assert busy.status_code == 503 and busy.json()["code"] == "data_dir_moving"
        # A second request neither starts another move nor ends this one's protection.
        again = client.put("/api/v1/settings", json={"data_dir": str(tmp_path / "second")})
        assert again.status_code == 409 and again.json()["code"] == "data_dir_job_running"
        assert client.app.state.moving is True
        assert client.get(f"/api/v1/jobs/{job['id']}").json()["status"] == "running"
    finally:
        gate.release.set()
    done = _until(client, job["id"], {"done", "error", "cancelled"})
    assert done["status"] == "done", done
    assert client.app.state.moving is False
    assert not journal_path(settings).exists()


def test_cancel_while_copying_keeps_the_library_where_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, client, lib = _library(tmp_path)
    boot = settings.bootstrap_dir
    gate = Gate()
    _hold_copy(monkeypatch, gate)
    dest = tmp_path / "newhome"
    job = _start(client, dest)
    assert gate.reached.wait(10)
    cancelled = client.post(f"/api/v1/jobs/{job['id']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    gate.release.set()
    done = _until(client, job["id"], {"done", "error", "cancelled"})
    assert done["status"] == "cancelled", done
    assert not any((dest / name).exists() for name in ("libraries", "backups", "exports"))
    assert (lib / "one.jpg").is_file()
    assert (boot / "backups" / "site.zip").is_file() and (boot / "exports" / "u1" / "e.zip").is_file()
    assert read_data_dir_config(boot) is None
    assert not journal_path(settings).exists()
    assert client.app.state.moving is False
    assert len(client.get("/api/v1/assets").json()["items"]) == 1


def test_a_copy_that_does_not_match_is_refused_and_undone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, client, lib = _library(tmp_path)
    boot = settings.bootstrap_dir
    real = config_mod._copy_path_bytes

    def lossy(src: Path, dest: Path, progress: object = None) -> None:
        real(src, dest, progress)  # type: ignore[arg-type]
        if src.name == "e.zip":
            dest.unlink()  # a file that never made it

    monkeypatch.setattr(config_mod, "_copy_path_bytes", lossy)
    dest = tmp_path / "newhome"
    job = _move(client, {"data_dir": str(dest)})
    assert job["status"] == "error" and "does not match" in job["error"], job
    assert not any((dest / name).exists() for name in ("libraries", "backups", "exports"))
    assert (boot / "exports" / "u1" / "e.zip").is_file() and (lib / "one.jpg").is_file()
    assert read_data_dir_config(boot) is None
    assert not journal_path(settings).exists()
    assert client.get("/api/v1/assets").status_code == 200


def test_the_job_result_tells_the_page_whether_the_api_restarts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _settings_, client, _lib = _library(tmp_path)
    monkeypatch.setattr("mosaicwave.api.v1.settings.schedule_api_restart", lambda _boot: True)
    job = _move(client, {"data_dir": str(tmp_path / "newhome")})
    assert job["status"] == "done" and job["restarting"] is True


def test_a_failed_cleanup_of_the_old_files_keeps_the_journal_for_the_next_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, client, _lib = _library(tmp_path)
    boot = settings.bootstrap_dir
    real = config_mod._delete_named

    def stuck(root: Path, names: list[str]) -> None:
        if root == boot.resolve():
            raise OSError("file in use")
        real(root, names)

    monkeypatch.setattr("mosaicwave.runtime._delete_named", stuck)
    dest = tmp_path / "newhome"
    job = _move(client, {"data_dir": str(dest)})
    assert job["status"] == "done", job  # the new folder is live; deleting the old one is best effort
    assert read_data_dir_config(boot) == dest.resolve()
    journal = read_journal(settings)
    assert journal is not None and (boot / "backups").exists()
    # The next start finishes the deletion.
    monkeypatch.undo()
    live = Settings(data_dir=dest, takeout_dir=None, source_dir=None, bootstrap_dir=boot)
    assert recover_data_dir_move(live) == "finished"
    assert not (boot / "backups").exists() and not journal_path(live).exists()
    assert (dest / "backups" / "site.zip").is_file()


# --- crash recovery, straight on the journal ---


def _folders(tmp_path: Path) -> tuple[Path, Path]:
    boot, dest = tmp_path / "boot", tmp_path / "new"
    for root in (boot, dest):
        root.mkdir()
    (boot / "libraries" / "u1").mkdir(parents=True)
    (boot / "libraries" / "u1" / "a.jpg").write_bytes(b"a")
    (boot / "backups").mkdir()
    (boot / "backups" / "s.zip").write_bytes(b"s")
    return boot, dest


def _copy_into(dest: Path) -> None:
    (dest / "libraries" / "u1").mkdir(parents=True)
    (dest / "libraries" / "u1" / "a.jpg").write_bytes(b"a")
    (dest / "backups").mkdir()
    (dest / "backups" / "s.zip").write_bytes(b"s")


def test_recovery_before_the_switch_removes_the_partial_copy(tmp_path: Path) -> None:
    boot, dest = _folders(tmp_path)
    _copy_into(dest)
    settings = Settings(data_dir=boot, takeout_dir=None, source_dir=None, bootstrap_dir=boot)
    write_journal(settings, boot, dest, ["libraries", "backups"], COPYING)
    assert recover_data_dir_move(settings) == "rolled_back"
    assert not (dest / "libraries").exists() and not (dest / "backups").exists()
    assert (boot / "libraries" / "u1" / "a.jpg").is_file() and (boot / "backups" / "s.zip").is_file()
    assert not journal_path(settings).exists()


def test_recovery_during_the_rewrite_points_the_databases_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    boot, dest = _folders(tmp_path)
    _copy_into(dest)
    (boot / "library.db").write_bytes(b"")
    settings = Settings(data_dir=boot, takeout_dir=None, source_dir=None, bootstrap_dir=boot)
    seen: list[tuple[Path, Path, Path]] = []
    monkeypatch.setattr(datamove, "_rewrite_library_root_uris", lambda db, old, new: seen.append((db, old, new)))
    write_journal(settings, boot, dest, ["libraries", "backups"], REWRITING)
    assert recover_data_dir_move(settings) == "rolled_back"
    assert seen == [(settings.database_path, dest, boot)]  # paths under the new folder go back to the old one
    assert not (dest / "libraries").exists() and (boot / "libraries" / "u1" / "a.jpg").is_file()


def test_recovery_after_the_switch_finishes_deleting_the_old_files(tmp_path: Path) -> None:
    boot, dest = _folders(tmp_path)
    _copy_into(dest)
    settings = Settings(data_dir=dest, takeout_dir=None, source_dir=None, bootstrap_dir=boot)
    write_journal(settings, boot, dest, ["libraries", "backups"], REWRITING)
    assert recover_data_dir_move(settings) == "finished"
    assert not (boot / "libraries").exists() and not (boot / "backups").exists()
    assert (dest / "libraries" / "u1" / "a.jpg").is_file() and (dest / "backups" / "s.zip").is_file()
    assert not journal_path(settings).exists()


def test_recovery_touches_nothing_when_the_journal_matches_neither_folder(tmp_path: Path) -> None:
    boot, dest = _folders(tmp_path)
    _copy_into(dest)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    settings = Settings(data_dir=elsewhere, takeout_dir=None, source_dir=None, bootstrap_dir=boot)
    write_journal(settings, boot, dest, ["libraries", "backups"], COPYING)
    assert recover_data_dir_move(settings) is None
    assert (boot / "libraries").exists() and (dest / "libraries").exists()
    assert journal_path(settings).exists()


def test_recovery_with_no_or_broken_journal_is_a_no_op(tmp_path: Path) -> None:
    boot, _dest = _folders(tmp_path)
    settings = Settings(data_dir=boot, takeout_dir=None, source_dir=None, bootstrap_dir=boot)
    assert recover_data_dir_move(settings) is None
    journal_path(settings).write_text("{not json", encoding="utf-8")
    assert recover_data_dir_move(settings) is None
    assert (boot / "libraries").exists()


def test_start_up_runs_the_recovery_before_opening_anything(tmp_path: Path) -> None:
    settings = _app_folder_settings(tmp_path)
    boot = settings.bootstrap_dir
    dest = tmp_path / "half"
    boot.mkdir(parents=True)
    (dest / "libraries" / "u1").mkdir(parents=True)
    (dest / "libraries" / "u1" / "partial.jpg").write_bytes(b"p")
    write_journal(settings, boot, dest, ["libraries"], COPYING)
    client = authed_client(settings)  # create_app: crash recovery first
    assert not (dest / "libraries").exists()
    assert not journal_path(settings).exists()
    assert client.get("/api/v1/me").status_code == 200
