from __future__ import annotations

import os
from pathlib import Path

import pytest

from mosaicwave.config import Settings
from mosaicwave.library.fsbrowse import list_host_dir
from authutil import authed_client
from fastapi.testclient import TestClient


def test_list_host_dir_tmp(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "takeout-20260101T000000Z-1-001.zip").write_bytes(b"PK")
    listing = list_host_dir(str(tmp_path))
    names = {e.name for e in listing.entries}
    assert "sub" in names
    assert "takeout-20260101T000000Z-1-001.zip" in names
    assert listing.takeout_zip_count == 1
    assert listing.parent == str(tmp_path.parent)


def test_fs_list_api(tmp_path: Path) -> None:
    (tmp_path / "photos").mkdir()
    client = authed_client(Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None))
    res = client.get("/api/v1/fs/list", params={"path": str(tmp_path)})
    assert res.status_code == 200, res.text
    body = res.json()
    assert any(e["name"] == "photos" and e["is_dir"] for e in body["entries"])
    missing = client.get("/api/v1/fs/list", params={"path": str(tmp_path / "nope")})
    assert missing.status_code == 404


def test_fs_list_hides_other_libraries(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    admin = authed_client(settings)
    created = admin.put(
        "/api/v1/users",
        json={"username": "pat", "password": "memberpass", "display_name": "Pat", "role": "member"},
    )
    assert created.status_code == 200
    member = TestClient(admin.app)
    assert member.post("/api/v1/auth/login", json={"username": "pat", "password": "memberpass"}).status_code == 200
    member.get("/api/v1/me")
    libraries = (settings.data_dir / "libraries").resolve()
    listing = member.get("/api/v1/fs/list", params={"path": str(libraries)})
    assert listing.status_code == 200, listing.text
    names = {e["name"] for e in listing.json()["entries"]}
    assert names == {member.get("/api/v1/me").json()["id"]}
    other = admin.get("/api/v1/me").json()["id"]
    blocked = member.get("/api/v1/fs/list", params={"path": str(libraries / other)})
    assert blocked.status_code == 403


@pytest.mark.skipif(os.name == "nt", reason="posix default root")
def test_list_host_dir_empty_uses_posix_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "Public").mkdir()
    monkeypatch.setattr("mosaicwave.library.fsbrowse.sys.platform", "linux")
    monkeypatch.setattr("mosaicwave.library.fsbrowse.posix_default_root", lambda: tmp_path)
    listing = list_host_dir(None)
    assert listing.path == str(tmp_path.resolve())
    assert any(e.name == "Public" for e in listing.entries)


@pytest.mark.skipif(os.name == "nt", reason="ignore Windows paths on the NAS")
def test_list_host_dir_ignores_windows_path_on_posix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mosaicwave.library.fsbrowse.sys.platform", "linux")
    monkeypatch.setattr("mosaicwave.library.fsbrowse.posix_default_root", lambda: tmp_path)
    listing = list_host_dir(r"D:\google photos")
    assert listing.path == str(tmp_path.resolve())


def test_list_host_dir_smb_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    public = tmp_path / "Public"
    (public / "mosaicWave").mkdir(parents=True)
    monkeypatch.setattr(
        "mosaicwave.library.fsbrowse.coerce_host_folder",
        lambda raw, mount=True: public,
    )
    listing = list_host_dir("smb://fileserver/Public")
    assert listing.path == str(public.resolve())
    assert any(e.name == "mosaicWave" and e.is_dir for e in listing.entries)


def test_list_host_dir_darwin_places(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    volumes = tmp_path / "Volumes"
    home.mkdir()
    volumes.mkdir()
    monkeypatch.setattr("mosaicwave.library.fsbrowse.os.name", "posix")
    monkeypatch.setattr("mosaicwave.library.fsbrowse.sys.platform", "darwin")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr("mosaicwave.library.fsbrowse._volume_root", lambda: volumes)
    listing = list_host_dir(None)
    names = {e.name: e.path for e in listing.entries}
    assert names["Home"] == str(home)
    assert names["Volumes"] == str(volumes)
    assert listing.path == ""
    assert listing.parent is None


def test_list_host_dir_darwin_places_lists_volume_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    volumes = tmp_path / "Volumes"
    public = volumes / "Public"
    home.mkdir()
    public.mkdir(parents=True)
    (public / "mosaicWave").mkdir()
    monkeypatch.setattr("mosaicwave.library.fsbrowse.os.name", "posix")
    monkeypatch.setattr("mosaicwave.library.fsbrowse.sys.platform", "darwin")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr("mosaicwave.library.fsbrowse._volume_root", lambda: volumes)
    listing = list_host_dir(None)
    names = {e.name: e.path for e in listing.entries}
    assert names["Public"] == str(public)


def test_list_host_dir_permission_keeps_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "Public"
    target.mkdir()
    (target / "hidden").mkdir()

    def boom(_path: str | os.PathLike[str]) -> list[str]:
        raise PermissionError("denied")

    monkeypatch.setattr("mosaicwave.library.fsbrowse.os.listdir", boom)
    listing = list_host_dir(str(target))
    assert listing.path == str(target.resolve())
    assert listing.entries == []
    assert listing.warning
    assert "permission" in listing.warning.lower()
    assert "Mac user" not in listing.warning


def test_list_host_dir_empty_volume_keeps_this_process_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "mosaicWave-fileserver-Public"
    target.mkdir()

    monkeypatch.setattr("mosaicwave.library.fsbrowse.os.listdir", lambda _p: [])
    monkeypatch.setattr("mosaicwave.library.fsbrowse._under_volumes", lambda _p: True)
    listing = list_host_dir(str(target))
    assert listing.entries == []
    assert listing.warning is None


def test_list_host_dir_app_volume_permission_is_system_dest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "mosaicWave-fileserver-Public"
    target.mkdir()

    def boom(_path: str | os.PathLike[str]) -> list[str]:
        raise PermissionError("denied")

    monkeypatch.setattr("mosaicwave.library.fsbrowse.os.listdir", boom)
    listing = list_host_dir(str(target))
    assert listing.entries == []
    assert listing.warning
    assert "previous mosaicWave dest" in listing.warning
    assert "Mac user" not in listing.warning


def test_list_host_dir_volumes_permission_rejects_finder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "Public"
    target.mkdir()

    def boom(_path: str | os.PathLike[str]) -> list[str]:
        raise PermissionError("denied")

    monkeypatch.setattr("mosaicwave.library.fsbrowse.os.listdir", boom)
    monkeypatch.setattr("mosaicwave.library.fsbrowse._under_volumes", lambda _p: True)
    listing = list_host_dir(str(target))
    assert listing.entries == []
    assert listing.warning
    assert "does not use Finder" in listing.warning
    assert "Mac user" not in listing.warning
