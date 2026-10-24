"""One user's library: back it up, restore it over the live one while the server runs (doc/backup.md)."""

from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from authutil import library_dir, me_id, scan_library
from test_backup import _make_member, _seed, _settings
from test_cancel import Gate, _until
from test_takeout import _jpeg_bytes

from mosaicwave import libbackup
from mosaicwave.archive import ArchiveError
from mosaicwave.backup import backup_site
from mosaicwave.config import Settings
from mosaicwave.library import importjob
from mosaicwave.library.dbs import LibraryLocked


def _backup(client: TestClient) -> str:
    res = client.post("/api/v1/me/backups")
    assert res.status_code == 200, res.text
    job = _until(client, res.json()["id"], {"done", "error", "cancelled"})
    assert job["status"] == "done", job
    return client.get("/api/v1/me/backups").json()[0]["name"]


def _restore(client: TestClient, name: str) -> dict:
    res = client.post(f"/api/v1/me/backups/{name}/restore")
    assert res.status_code == 200, res.text
    return _until(client, res.json()["id"], {"done", "error", "cancelled"})


def _assets(client: TestClient) -> int:
    return len(client.get("/api/v1/assets").json()["items"])


def _add_photo(settings: Settings, client: TestClient, name: str = "late.jpg") -> None:
    (library_dir(settings, client) / name).write_bytes(_jpeg_bytes((10, 200, 10)))
    scan_library(client)


def _member_with_photo(admin: TestClient, settings: Settings) -> TestClient:
    member = _make_member(admin)
    _add_photo(settings, member, "mine.jpg")
    assert _assets(member) == 1
    return member


def _leftovers(settings: Settings) -> list[Path]:
    found = set(settings.database_path.parent.glob(".restore-staging-*"))
    found |= set(settings.data_dir.glob(".restore-staging-*"))
    found |= set(settings.database_path.parent.glob("library-swap-*.json"))
    return sorted(found)


def test_round_trip_restores_the_exact_library_and_keeps_the_old_one(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    uid = me_id(client)
    name = _backup(client)
    assert name.startswith("mosaicwave-library-") and name.endswith(".zip")
    archive = settings.backup_dir / "users" / uid / name
    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
    assert {"manifest.json", "library.db"} <= names
    assert not [n for n in names if n not in ("manifest.json", "library.db") and not n.startswith(f"libraries/{uid}/")]

    listed = client.get("/api/v1/me/backups").json()
    assert [(b["name"], b["assets"], b["albums"]) for b in listed] == [(name, 2, 1)]

    _add_photo(settings, client)
    assert _assets(client) == 3

    job = _restore(client, name)
    assert job["status"] == "done", job
    assert _assets(client) == 2
    albums = client.get("/api/v1/albums").json()
    assert [(a["name"], a["asset_count"]) for a in albums] == [("Holiday", 1)]
    assert not (library_dir(settings, client) / "late.jpg").exists()
    assert (library_dir(settings, client) / "sub" / "blue.jpg").is_file()
    assert not _leftovers(settings)

    kept = list((settings.database_path.parent / "before-restore").glob(f"*-{uid}-db")) + list(
        (settings.data_dir / "before-restore").glob(f"*-{uid}-files")
    )
    assert len(kept) == 2 and any((p / "late.jpg").exists() for p in kept)

    # The restored library has no thumbnails; a photo still opens (the server rebuilds it).
    first = client.get("/api/v1/assets").json()["items"][0]
    assert client.get(f"/api/v1/assets/{first['id']}/thumb").status_code == 200


def test_restore_changes_nothing_for_other_users(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    admin = _seed(settings)
    member = _member_with_photo(admin, settings)
    name = _backup(member)
    _add_photo(settings, member)
    assert _assets(member) == 2

    assert _restore(member, name)["status"] == "done"
    assert _assets(member) == 1
    assert _assets(admin) == 2
    assert admin.get("/api/v1/albums").json()[0]["asset_count"] == 1


def test_users_only_see_their_own_archives(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    admin = _seed(settings)
    member = _member_with_photo(admin, settings)
    name = _backup(member)

    assert admin.get("/api/v1/me/backups").json() == []
    for call in (
        admin.get(f"/api/v1/me/backups/{name}"),
        admin.delete(f"/api/v1/me/backups/{name}"),
        admin.post(f"/api/v1/me/backups/{name}/restore"),
    ):
        assert call.status_code == 404 and call.json()["code"] == "backup_not_found"
    assert admin.get("/api/v1/backups").json() == []  # the site list holds site archives only
    assert member.get(f"/api/v1/me/backups/{name}").status_code == 200
    assert member.get("/api/v1/me/backups/not-a-name.zip").status_code == 404

    assert member.delete(f"/api/v1/me/backups/{name}").json() == {"ok": True}
    assert member.get("/api/v1/me/backups").json() == []


def test_signed_out_is_refused(tmp_path: Path) -> None:
    client = _seed(_settings(tmp_path))
    anon = TestClient(client.app)
    assert anon.get("/api/v1/me/backups").status_code == 401
    assert anon.post("/api/v1/me/backups").status_code == 401
    assert anon.put("/api/v1/me/backups/upload", content=b"x").status_code == 401


def test_an_archive_restores_into_a_different_account(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    admin = _seed(settings)
    member = _member_with_photo(admin, settings)
    name = _backup(admin)
    data = (settings.backup_dir / "users" / me_id(admin) / name).read_bytes()

    up = member.put("/api/v1/me/backups/upload", content=data)
    assert up.status_code == 200, up.text
    assert up.json()["name"] == name and up.json()["assets"] == 2

    assert _restore(member, name)["status"] == "done"
    assert _assets(member) == 2
    assert [a["name"] for a in member.get("/api/v1/albums").json()] == ["Holiday"]
    mine = library_dir(settings, member)
    assert (mine / "sub" / "blue.jpg").is_file() and not (mine / "mine.jpg").exists()
    # every source belongs to the member and points inside the member's folder
    con = sqlite3.connect(settings.accounts_dir / me_id(member) / "library.db")
    try:
        rows = con.execute("SELECT owner_user_id, root_uri FROM source").fetchall()
    finally:
        con.close()
    assert rows and all(owner == me_id(member) for owner, _ in rows)
    assert all(Path(uri).resolve().is_relative_to(mine) for _, uri in rows)
    assert _assets(admin) == 2 and (library_dir(settings, admin) / "red.jpg").is_file()
    assert member.get("/api/v1/sources").json()  # the member can still scan their library
    _add_photo(settings, member, "after.jpg")
    assert _assets(member) == 3


def test_restore_makes_every_sync_client_pull_again(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    name = _backup(client)
    _add_photo(settings, client)
    before = client.get("/api/v1/sync/changes", params={"since_rev": 0}).json()["server_rev"]

    assert _restore(client, name)["status"] == "done"
    body = client.get("/api/v1/sync/changes", params={"since_rev": before}).json()
    assert len(body["assets"]) == 2 and len(body["albums"]) == 1
    assert all(a["rev"] > before for a in body["assets"])
    assert body["server_rev"] > before


def test_site_restore_also_moves_revs_above_the_live_library(tmp_path: Path) -> None:
    from mosaicwave.restore import live_max_rev, restore_site

    settings = _settings(tmp_path)
    client = _seed(settings)
    archive = backup_site(settings, tmp_path / "out").path
    _add_photo(settings, client)
    uid = me_id(client)
    db = settings.accounts_dir / uid / "library.db"
    live = live_max_rev(db)
    client.app.state.library_dbs.dispose_all()
    client.app.state.engine.dispose()

    restore_site(archive, settings)
    assert live_max_rev(settings.accounts_dir / uid / "library.db") > live


def test_wrong_kind_is_refused_both_ways(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    site = backup_site(settings, tmp_path / "out").path.read_bytes()
    name = _backup(client)
    library = (settings.backup_dir / "users" / me_id(client) / name).read_bytes()

    a = client.put("/api/v1/me/backups/upload", content=site)
    assert a.status_code == 400 and a.json()["code"] == "backup_wrong_kind", a.text
    b = client.put("/api/v1/backups/upload", content=library)
    assert b.status_code == 400 and b.json()["code"] == "backup_wrong_kind", b.text
    assert not list((settings.backup_dir / "users" / me_id(client)).glob(".upload-*"))
    assert not list(settings.backup_dir.glob(".upload-*")) and not list(settings.backup_dir.glob("*.zip"))
    with pytest.raises(ArchiveError):
        libbackup.check_library_archive(backup_site(settings, tmp_path / "x").path)


def test_a_newer_library_schema_is_refused(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    name = _backup(client)
    src = settings.backup_dir / "users" / me_id(client) / name
    bad = tmp_path / "newer.zip"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(bad, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info)
            if info.filename == "manifest.json":
                manifest = json.loads(data)
                manifest["library_schema_version"] = 99
                data = json.dumps(manifest).encode()
            zout.writestr(info, data)
    with pytest.raises(ArchiveError):
        libbackup.check_library_archive(bad)
    res = client.put("/api/v1/me/backups/upload", content=bad.read_bytes())
    assert res.status_code == 400 and res.json()["code"] == "backup_invalid"
    assert _assets(client) == 2


def test_cancel_while_staging_leaves_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    name = _backup(client)
    _add_photo(settings, client)

    gate = Gate()
    real = importjob.stage_library

    def gated(archive, settings, user_id, *, on_progress=None):  # type: ignore[no-untyped-def]
        def step(done: int, total: int, rel: str) -> None:
            gate.step()
            on_progress(done, total, rel)

        return real(archive, settings, user_id, on_progress=step)

    monkeypatch.setattr(importjob, "stage_library", gated)
    job = client.post(f"/api/v1/me/backups/{name}/restore").json()
    assert gate.reached.wait(10)
    assert client.post(f"/api/v1/jobs/{job['id']}/cancel").status_code == 200
    gate.release.set()

    done = _until(client, job["id"], {"cancelled", "done", "error"})
    assert done["status"] == "cancelled", done
    assert not _leftovers(settings)
    assert _assets(client) == 3 and (library_dir(settings, client) / "late.jpg").exists()


def test_cancel_during_backup_writes_no_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    gate = Gate()
    real = importjob.backup_library

    def gated(settings, user_id, out_dir, *, on_progress=None):  # type: ignore[no-untyped-def]
        def step(done: int, total: int, rel: str) -> None:
            gate.step()
            on_progress(done, total, rel)

        return real(settings, user_id, out_dir, on_progress=step)

    monkeypatch.setattr(importjob, "backup_library", gated)
    job = client.post("/api/v1/me/backups").json()
    assert gate.reached.wait(10)
    assert client.post(f"/api/v1/jobs/{job['id']}/cancel").status_code == 200
    gate.release.set()
    assert _until(client, job["id"], {"cancelled", "done", "error"})["status"] == "cancelled"
    folder = settings.backup_dir / "users" / me_id(client)
    assert not folder.exists() or not list(folder.iterdir())


def test_a_failed_swap_rolls_back_and_the_library_still_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    name = _backup(client)
    _add_photo(settings, client)
    calls: list[str] = []
    real = libbackup._rename

    def flaky(src: Path, dest: Path, *, wait: float) -> None:
        calls.append(src.name)
        if len(calls) == 2:  # the database folder moved, the originals cannot
            raise ArchiveError("disk said no")
        real(src, dest, wait=wait)

    monkeypatch.setattr(libbackup, "_rename", flaky)
    job = _restore(client, name)
    assert job["status"] == "error" and "disk said no" in job["error"]
    assert _assets(client) == 3
    assert (library_dir(settings, client) / "late.jpg").is_file()
    assert not _leftovers(settings)
    _add_photo(settings, client, "again.jpg")
    assert _assets(client) == 4


def test_a_file_in_use_is_retried_then_rolled_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    uid = me_id(client)
    name = _backup(client)
    _add_photo(settings, client)
    staged = libbackup.stage_library(settings.backup_dir / "users" / uid / name, settings, uid)

    real = Path.rename
    attempts = {"n": 0}

    def busy(self: Path, target):  # type: ignore[no-untyped-def]
        if self.name == uid and self.parent.name == "accounts":
            attempts["n"] += 1
            raise PermissionError("in use")
        return real(self, target)

    monkeypatch.setattr(Path, "rename", busy)
    with pytest.raises(ArchiveError, match="in use"):
        libbackup.swap_library(staged, settings, client.app.state.library_dbs, wait=0.6)
    assert attempts["n"] >= 2  # it waited and tried again
    monkeypatch.undo()
    assert not _leftovers(settings)
    assert _assets(client) == 3 and (library_dir(settings, client) / "late.jpg").is_file()


def test_a_brief_file_in_use_is_waited_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    uid = me_id(client)
    name = _backup(client)
    _add_photo(settings, client)
    staged = libbackup.stage_library(settings.backup_dir / "users" / uid / name, settings, uid)

    real = Path.rename
    attempts = {"n": 0}

    def busy_twice(self: Path, target):  # type: ignore[no-untyped-def]
        if self.name == uid and self.parent.name == "accounts" and attempts["n"] < 2:
            attempts["n"] += 1
            raise PermissionError("in use")
        return real(self, target)

    monkeypatch.setattr(Path, "rename", busy_twice)
    libbackup.swap_library(staged, settings, client.app.state.library_dbs, wait=5)
    monkeypatch.undo()
    assert attempts["n"] == 2
    assert _assets(client) == 2


def test_requests_for_a_library_being_swapped_get_503_others_do_not(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    admin = _seed(settings)
    member = _member_with_photo(admin, settings)
    uid = me_id(member)
    dbs = admin.app.state.library_dbs
    with dbs.locked(uid):
        mine = member.get("/api/v1/assets")
        assert mine.status_code == 503 and mine.json()["code"] == "library_restoring"
        assert mine.headers["retry-after"] == "2"
        assert _assets(admin) == 2
        with pytest.raises(LibraryLocked):
            with dbs.locked(uid):
                pass
    assert _assets(member) == 1


def test_a_crash_mid_swap_is_undone_at_start(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    uid = me_id(client)
    client.app.state.library_dbs.dispose_all()
    client.app.state.engine.dispose()

    aside_db = settings.database_path.parent / "before-restore" / f"20260101-000000-{uid}-db"
    aside_files = settings.data_dir / "before-restore" / f"20260101-000000-{uid}-files"
    aside_db.parent.mkdir(parents=True, exist_ok=True)
    aside_files.parent.mkdir(parents=True, exist_ok=True)
    (settings.accounts_dir / uid).rename(aside_db)
    (settings.data_dir / "libraries" / uid).rename(aside_files)
    # the new library was only half moved in
    (settings.accounts_dir / uid).mkdir()
    (settings.accounts_dir / uid / "library.db").write_bytes(b"half")
    libbackup._journal(settings, uid).write_text(
        json.dumps({"user_id": uid, "stamp": "20260101-000000", "aside_db": str(aside_db), "aside_files": str(aside_files)})
    )

    assert libbackup.recover_library_swaps(settings) == [uid]
    assert not libbackup._journal(settings, uid).exists()
    from authutil import authed_client

    fresh = authed_client(settings)
    assert _assets(fresh) == 2
    assert (library_dir(settings, fresh) / "sub" / "blue.jpg").is_file()


def test_start_clears_leftover_user_staging(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    uid = me_id(client)
    a = settings.database_path.parent / f".restore-staging-user-20260101-000000-{uid}"
    b = settings.data_dir / f".restore-staging-userdata-20260101-000000-{uid}"
    for folder in (a, b):
        folder.mkdir(parents=True)
        (folder / "x").write_text("x")
    libbackup.remove_stale_user_staging(settings)
    assert not a.exists() and not b.exists()


def test_an_empty_library_backs_up_and_restore_needs_a_free_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    admin = _seed(settings)
    fresh = _make_member(admin)  # signed in, nothing scanned yet
    res = fresh.post("/api/v1/me/backups")
    assert res.status_code == 200
    job = _until(fresh, res.json()["id"], {"done", "error"})
    assert job["status"] == "done" and job["assets"] == 0
    assert [(b["assets"], b["file_count"]) for b in fresh.get("/api/v1/me/backups").json()] == [(0, 0)]

    name = _backup(admin)
    gate = Gate()
    real = importjob.backup_library

    def hold(settings, user_id, out_dir, *, on_progress=None):  # type: ignore[no-untyped-def]
        def step(done: int, total: int, rel: str) -> None:
            gate.step()
            on_progress(done, total, rel)

        return real(settings, user_id, out_dir, on_progress=step)

    monkeypatch.setattr(importjob, "backup_library", hold)
    running = admin.post("/api/v1/me/backups").json()
    assert gate.reached.wait(10)
    busy = admin.post(f"/api/v1/me/backups/{name}/restore")
    assert busy.status_code == 409 and busy.json()["code"] == "job_running"
    gate.release.set()
    _until(admin, running["id"], {"done", "error"})
