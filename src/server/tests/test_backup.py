from __future__ import annotations

import json
import sqlite3
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from authutil import authed_client, library_dir, scan_library
from test_takeout import _jpeg_bytes

from mosaicwave import backup as backup_cli
from mosaicwave import restore as restore_cli
from mosaicwave import schema
from mosaicwave.archive import ArchiveError, safe_member
from mosaicwave.backup import backup_site
from mosaicwave.config import Settings
from mosaicwave.restore import check_archive, restore_site


def _settings(root: Path) -> Settings:
    return Settings(data_dir=root / "data", takeout_dir=None, source_dir=None)


def _seed(settings: Settings) -> TestClient:
    """An admin with two photos and one album holding one of them."""
    client = authed_client(settings)
    lib = library_dir(settings, client)
    (lib / "red.jpg").write_bytes(_jpeg_bytes((200, 30, 30)))
    (lib / "sub").mkdir()
    (lib / "sub" / "blue.jpg").write_bytes(_jpeg_bytes((30, 30, 200)))
    scan_library(client)
    items = client.get("/api/v1/assets").json()["items"]
    assert len(items) == 2
    album = client.post("/api/v1/albums", json={"name": "Holiday"}).json()
    added = client.post(f"/api/v1/albums/{album['id']}/assets", json={"asset_ids": [items[0]["id"]]})
    assert added.status_code == 200, added.text
    return client


def _dispose(client: TestClient) -> None:
    client.app.state.library_dbs.dispose_all()
    client.app.state.engine.dispose()


def _names(archive: Path) -> list[str]:
    with zipfile.ZipFile(archive) as zf:
        return zf.namelist()


def _fake_archive(path: Path, *, manifest: dict | None = None, db: bytes | None = None,
                  extra: dict[str, bytes] | None = None) -> Path:
    base = {"format": 1, "kind": "site", "created_at": "2026-01-01T00:00:00+00:00", "app_version": "0",
            "include_thumbs": False, "file_count": 0, "total_bytes": 0, "warnings": [],
            "schema_version": schema.CURRENT_VERSION, "library_schema_version": 1,
            "user_ids": [], "libraries": {}, "users": 0, "assets": 0, "albums": 0}
    base.update(manifest or {})
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(base))
        if db is not None:
            zf.writestr("library.db", db)
        for name, data in (extra or {}).items():
            zf.writestr(zipfile.ZipInfo(name), data)
    return path


def test_round_trip_into_empty_data_folder(tmp_path: Path) -> None:
    src = _settings(tmp_path / "a")
    client = _seed(src)
    (src.data_dir / "session.key").write_text("secret")
    result = backup_site(src, tmp_path / "out")

    names = _names(result.path)
    uid = client.get("/api/v1/me").json()["id"]
    fixed = {"manifest.json", "library.db", f"accounts/{uid}/library.db"}
    assert fixed <= set(names)
    assert not [n for n in names if n not in fixed and not n.startswith("libraries/")]
    assert not any(n.endswith(("session.key", "config.json", "nsmb.conf")) or "thumbs" in n for n in names)
    assert result.manifest["assets"] == 2 and result.manifest["albums"] == 1
    assert result.manifest["file_count"] == 2

    dst = _settings(tmp_path / "b")
    done = restore_site(result.path, dst)
    assert done.site_upgrade_from is None and done.library_upgrade_from is None

    restored = authed_client(dst)
    assert len(restored.get("/api/v1/assets").json()["items"]) == 2
    albums = restored.get("/api/v1/albums").json()
    assert [(a["name"], a["asset_count"]) for a in albums] == [("Holiday", 1)]
    assert (dst.data_dir / "libraries" / uid / "sub" / "blue.jpg").is_file()
    client.close()


def test_restore_over_existing_data_keeps_the_old_copy(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    archive = backup_site(settings, tmp_path / "out").path

    lib = library_dir(settings, client)
    (lib / "late.jpg").write_bytes(_jpeg_bytes((10, 200, 10)))
    scan_library(client)
    assert len(client.get("/api/v1/assets").json()["items"]) == 3
    _dispose(client)

    done = restore_site(archive, settings)
    assert done.kept and all(p.exists() for p in done.kept)
    after = authed_client(settings)
    assert len(after.get("/api/v1/assets").json()["items"]) == 2
    assert not (library_dir(settings, after) / "late.jpg").exists()
    assert not list(settings.data_dir.glob(".restore-staging-*"))
    assert any(list(p.rglob("late.jpg")) for p in done.kept if p.is_dir())


def test_archive_from_before_the_split_is_refused(tmp_path: Path) -> None:
    archive = _fake_archive(tmp_path / "old.zip", db=b"x", manifest={"schema_version": 2})
    with pytest.raises(ArchiveError):
        restore_site(archive, _settings(tmp_path))


def test_newer_schema_archive_is_refused(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    _dispose(client)
    for field in ("schema_version", "library_schema_version"):
        archive = _fake_archive(tmp_path / f"new-{field}.zip", db=b"x", manifest={field: 99})
        with pytest.raises(ArchiveError):
            restore_site(archive, settings)
    assert len(authed_client(settings).get("/api/v1/assets").json()["items"]) == 2


@pytest.mark.parametrize("name", ["libraries/../../evil.txt", "/abs.txt", "libraries\\x.jpg", "other/x.jpg", "libraries/C:/x"])
def test_unsafe_member_names_are_refused(tmp_path: Path, name: str) -> None:
    with pytest.raises(ArchiveError):
        safe_member(name)
    if "\\" in name:
        return  # zipfile itself rewrites backslashes on Windows, so no such archive can be built there
    archive = _fake_archive(tmp_path / "bad.zip", db=b"x", extra={name: b"x"}, manifest={"file_count": 1})
    settings = _settings(tmp_path / "dst")
    with pytest.raises(ArchiveError):
        restore_site(archive, settings)
    assert not (tmp_path / "evil.txt").exists() and not (tmp_path / "abs.txt").exists()


def test_damaged_archives_change_nothing(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    archive = backup_site(settings, tmp_path / "out").path
    _dispose(client)

    data = bytearray(archive.read_bytes())
    payload = _jpeg_bytes((200, 30, 30))
    at = bytes(data).find(payload)
    assert at > 0
    data[at + 10] ^= 0xFF
    flipped = tmp_path / "flipped.zip"
    flipped.write_bytes(bytes(data))
    with pytest.raises(ArchiveError):
        check_archive(flipped, full=True)
    with pytest.raises(ArchiveError):
        restore_site(flipped, settings)

    cut = tmp_path / "cut.zip"
    cut.write_bytes(bytes(data)[: len(data) // 2])
    with pytest.raises(ArchiveError):
        restore_site(cut, settings)

    assert len(authed_client(settings).get("/api/v1/assets").json()["items"]) == 2
    assert not list(settings.data_dir.glob("*.before-restore-*"))


def test_manifest_file_count_must_match(tmp_path: Path) -> None:
    archive = _fake_archive(tmp_path / "m.zip", db=b"x", manifest={"file_count": 5})
    with pytest.raises(ArchiveError):
        restore_site(archive, _settings(tmp_path / "dst"))


def test_locked_database_is_refused(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    archive = backup_site(settings, tmp_path / "out").path
    _dispose(client)
    holder = sqlite3.connect(settings.database_path, isolation_level=None)
    try:
        holder.execute("BEGIN EXCLUSIVE")
        with pytest.raises(ArchiveError):
            restore_site(archive, settings)
    finally:
        holder.close()


def test_failed_swap_rolls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    archive = backup_site(settings, tmp_path / "out").path
    _dispose(client)

    import shutil

    def boom(*_a, **_k):
        raise OSError("disk went away")

    monkeypatch.setattr(restore_cli.shutil, "move", boom)
    with pytest.raises(OSError):
        restore_site(archive, settings)
    monkeypatch.setattr(restore_cli.shutil, "move", shutil.move)
    assert len(authed_client(settings).get("/api/v1/assets").json()["items"]) == 2


def test_cli_backup_and_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setenv("MOSAICWAVE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("MOSAICWAVE_TAKEOUT_DIR", raising=False)
    monkeypatch.delenv("MOSAICWAVE_SOURCE_DIR", raising=False)
    _seed(Settings.from_env())
    out = tmp_path / "cli-out"
    assert backup_cli.main(["--out", str(out)]) == 0
    archive = next(out.glob("mosaicwave-site-*.zip"))
    assert restore_cli.main([str(archive), "--check"]) == 0
    assert "2 photos/videos" in capsys.readouterr().out
    assert restore_cli.main([str(tmp_path / "missing.zip"), "--check"]) == 1


def _make_member(admin: TestClient) -> TestClient:
    res = admin.put("/api/v1/users", json={"username": "pat", "password": "memberpass",
                                          "display_name": "Pat", "role": "member"})
    assert res.status_code == 200, res.text
    member = TestClient(admin.app)
    assert member.post("/api/v1/auth/login", json={"username": "pat", "password": "memberpass"}).status_code == 200
    return member


def test_backup_api_is_admin_only_and_lists_downloads_deletes(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    admin = _seed(settings)
    member = _make_member(admin)

    assert member.post("/api/v1/backups").status_code == 403
    assert member.get("/api/v1/backups").status_code == 403
    assert admin.get("/api/v1/backups").json() == []

    started = admin.post("/api/v1/backups", json={"include_thumbs": False})
    assert started.status_code == 200, started.text
    deadline = time.monotonic() + 20
    job = started.json()
    while time.monotonic() < deadline:
        job = admin.get(f"/api/v1/jobs/{job['id']}").json()
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert job["status"] == "done", job
    assert job["kind"] == "site_backup"

    listed = admin.get("/api/v1/backups").json()
    assert len(listed) == 1 and listed[0]["assets"] == 2 and listed[0]["albums"] == 1
    name = listed[0]["name"]

    assert member.get(f"/api/v1/backups/{name}").status_code == 403
    got = admin.get(f"/api/v1/backups/{name}")
    assert got.status_code == 200 and got.content[:2] == b"PK"

    for bad in ("..%5Csecret.zip", "notes.zip", "mosaicwave-site-20200101-000000.zip"):
        res = admin.get(f"/api/v1/backups/{bad}")
        assert res.status_code == 404
        assert res.json()["code"] == "backup_not_found"
    assert admin.delete(f"/api/v1/backups/{name}").status_code == 200
    assert admin.get("/api/v1/backups").json() == []


def test_every_users_library_is_backed_up_and_restored_separately(tmp_path: Path) -> None:
    src = _settings(tmp_path / "a")
    admin = _seed(src)
    member = _make_member(admin)
    member_lib = library_dir(src, member)
    (member_lib / "pat.jpg").write_bytes(_jpeg_bytes((90, 90, 90)))
    scan_library(member)

    archive = backup_site(src, tmp_path / "out")
    assert archive.manifest["users"] == 2 and archive.manifest["assets"] == 3
    assert sorted(archive.manifest["libraries"]) == sorted(archive.manifest["user_ids"])
    assert len(archive.manifest["user_ids"]) == 2
    _dispose(admin)

    dst = _settings(tmp_path / "b")
    restore_site(archive.path, dst)
    first = authed_client(dst)
    assert len(first.get("/api/v1/assets").json()["items"]) == 2
    second = TestClient(first.app)
    login = second.post("/api/v1/auth/login", json={"username": "pat", "password": "memberpass"})
    assert login.status_code == 200
    assert [i["filename"] for i in second.get("/api/v1/assets").json()["items"]] == ["pat.jpg"]
    assert second.get("/api/v1/albums").json() == []


def test_library_that_does_not_match_the_manifest_is_refused(tmp_path: Path) -> None:
    src = _settings(tmp_path / "a")
    admin = _seed(src)
    archive = backup_site(src, tmp_path / "out")
    _dispose(admin)

    forged = tmp_path / "forged.zip"
    with zipfile.ZipFile(archive.path) as zin, zipfile.ZipFile(forged, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "manifest.json":
                manifest = json.loads(data)
                manifest["assets"] = 3
                (uid,) = manifest["user_ids"]
                manifest["libraries"][uid]["assets"] = 3
                data = json.dumps(manifest).encode()
            zout.writestr(info.filename, data)
    dst = _settings(tmp_path / "b")
    with pytest.raises(ArchiveError):
        restore_site(forged, dst)
    assert not dst.database_path.exists()
    assert not list(dst.data_dir.glob(".restore-staging-*"))


def test_databases_in_the_app_folder_and_photos_in_the_data_folder(tmp_path: Path) -> None:
    """The NAS layout: SQLite stays in the app folder, the photos are on another volume."""
    src = Settings(
        data_dir=tmp_path / "nas-a", bootstrap_dir=tmp_path / "app-a", takeout_dir=None, source_dir=None
    )
    client = _seed(src)
    archive = backup_site(src, src.backup_dir)
    assert archive.path.parent == tmp_path / "app-a" / "backups"
    _dispose(client)

    dst = Settings(
        data_dir=tmp_path / "nas-b", bootstrap_dir=tmp_path / "app-b", takeout_dir=None, source_dir=None
    )
    restore_site(archive.path, dst)
    assert dst.database_path.parent == tmp_path / "app-b"
    assert list((tmp_path / "app-b" / "accounts").glob("*/library.db"))
    assert not (tmp_path / "nas-b" / "accounts").exists()
    restored = authed_client(dst)
    items = restored.get("/api/v1/assets").json()["items"]
    assert len(items) == 2
    assert restored.get(f"/api/v1/assets/{items[0]['id']}/file").status_code == 200