from __future__ import annotations

import json
import sqlite3
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from mosaicwave.config import Settings, read_data_dir_config
from authutil import authed_client, library_dir, scan_library


def _jpeg_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (8, 8), (40, 120, 200)).save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        takeout_dir=None,
        source_dir=None,
        bootstrap_dir=tmp_path / "boot",
    )


def test_settings_admin_only(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    admin = authed_client(settings)
    got = admin.get("/api/v1/settings")
    assert got.status_code == 200, got.text
    body = got.json()
    assert Path(body["data_dir"]) == settings.data_dir.resolve()
    assert Path(body["default_data_dir"]) == settings.bootstrap_dir.resolve()
    assert Path(body["app_dir"]) == settings.bootstrap_dir.resolve()
    assert body.get("data_dir_smb") in (None, "")
    assert body.get("data_dir_mount") in (None, "")
    assert body["env_override"] is False

    created = admin.put(
        "/api/v1/users",
        json={"username": "pat", "password": "memberpass", "display_name": "Pat", "role": "member"},
    )
    assert created.status_code == 200, created.text
    member = TestClient(admin.app)
    login = member.post("/api/v1/auth/login", json={"username": "pat", "password": "memberpass"})
    assert login.status_code == 200, login.text
    assert member.get("/api/v1/settings").status_code == 403
    assert (
        member.put("/api/v1/settings", json={"data_dir": str(tmp_path / "other")}).status_code == 403
    )


def test_put_settings_relocates_library(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = authed_client(settings)
    lib = library_dir(settings, client)
    (lib / "kept.jpg").write_bytes(_jpeg_bytes())
    scan_library(client)
    listed = client.get("/api/v1/assets").json()["items"]
    assert len(listed) == 1
    asset_id = listed[0]["id"]

    dest = tmp_path / "moved"
    res = client.put("/api/v1/settings", json={"data_dir": str(dest)})
    assert res.status_code == 200, res.text
    assert res.json().get("restarting") is False
    assert Path(res.json()["data_dir"]) == dest.resolve()
    assert res.json().get("data_dir_smb") in (None, "")
    assert res.json().get("data_dir_mount") in (None, "")
    assert (settings.bootstrap_dir / "library.db").is_file()
    assert (dest / "libraries" / Path(lib).name / "kept.jpg").is_file()
    assert not (dest / "library.db").exists()
    assert read_data_dir_config(settings.bootstrap_dir) == dest.resolve()
    assert (settings.bootstrap_dir / "config.json").is_file()

    again = client.get("/api/v1/assets")
    assert again.status_code == 200, again.text
    assert again.json()["items"][0]["id"] == asset_id
    thumb = client.get(f"/api/v1/assets/{asset_id}/thumb")
    assert thumb.status_code == 200
    assert client.get("/api/v1/settings").json()["data_dir"] == str(dest.resolve())
    assert (settings.bootstrap_dir / "session.key").is_file()
    assert not (dest / "session.key").exists()
    assert client.get("/api/v1/me").status_code == 200


def test_put_settings_rejects_occupied_and_relative(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = authed_client(settings)
    relative = client.put("/api/v1/settings", json={"data_dir": "not-absolute"})
    assert relative.status_code == 400

    occupied = tmp_path / "taken"
    occupied.mkdir()
    (occupied / "libraries").mkdir()
    (occupied / "libraries" / "kept.jpg").write_bytes(b"jpeg")
    clash = client.put("/api/v1/settings", json={"data_dir": str(occupied)})
    assert clash.status_code == 409

    nested = client.put(
        "/api/v1/settings",
        json={"data_dir": str(settings.data_dir / "inside")},
    )
    assert nested.status_code == 400


def test_put_settings_move_oserror_is_400(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    client = authed_client(settings)

    def boom(_src: Path, _dest: Path, **_kwargs: object) -> None:
        raise OSError("NAS chmod")

    monkeypatch.setattr("mosaicwave.runtime.copy_data_dir", boom)
    dest = tmp_path / "nas"
    src_db = settings.bootstrap_dir / "library.db"
    assert src_db.is_file()
    res = client.put("/api/v1/settings", json={"data_dir": str(dest)})
    assert res.status_code == 400, res.text
    assert "NAS chmod" in res.json()["detail"]
    assert Path(client.get("/api/v1/settings").json()["data_dir"]) == settings.data_dir.resolve()
    assert src_db.is_file()
    assert client.get("/api/v1/me").status_code == 200
    assert read_data_dir_config(settings.bootstrap_dir) is None


def test_put_settings_blocked_when_env_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    client = authed_client(settings)
    monkeypatch.setenv("MOSAICWAVE_DATA_DIR", str(tmp_path / "env-dir"))
    res = client.put("/api/v1/settings", json={"data_dir": str(tmp_path / "moved")})
    assert res.status_code == 409
    assert client.get("/api/v1/settings").json()["env_override"] is True
    monkeypatch.delenv("MOSAICWAVE_DATA_DIR", raising=False)
    # same path is a no-op relocate and writes config.json
    same = client.put("/api/v1/settings", json={"data_dir": str(settings.data_dir.resolve())})
    assert same.status_code == 200, same.text
    assert read_data_dir_config(settings.bootstrap_dir) == settings.data_dir.resolve()


def test_put_settings_returns_to_app_folder(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = authed_client(settings)
    dest = tmp_path / "moved"
    away = client.put("/api/v1/settings", json={"data_dir": str(dest)})
    assert away.status_code == 200, away.text
    assert (settings.bootstrap_dir / "library.db").is_file()
    (settings.bootstrap_dir / "libraries").mkdir(exist_ok=True)
    (settings.bootstrap_dir / "libraries" / "old.txt").write_text("stale", encoding="utf-8")
    home = client.put("/api/v1/settings", json={"data_dir": str(settings.bootstrap_dir)})
    assert home.status_code == 200, home.text
    body = home.json()
    assert Path(body["data_dir"]) == settings.bootstrap_dir.resolve()
    assert Path(body["app_dir"]) == settings.bootstrap_dir.resolve()
    assert (settings.bootstrap_dir / "library.db").is_file()
    assert (settings.bootstrap_dir / "session.key").is_file()
    assert not (dest / "library.db").exists()
    assert not (settings.bootstrap_dir / "libraries" / "old.txt").exists()
    assert client.get("/api/v1/me").status_code == 200


def test_put_settings_allows_empty_leftover_dest(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = authed_client(settings)
    dest = tmp_path / "emptyish"
    dest.mkdir()
    sqlite3.connect(dest / "library.db").close()
    (dest / "libraries").mkdir()
    res = client.put("/api/v1/settings", json={"data_dir": str(dest)})
    assert res.status_code == 200, res.text
    assert Path(res.json()["data_dir"]) == dest.resolve()
    assert (settings.bootstrap_dir / "library.db").is_file()


def test_put_settings_keeps_smb_source_and_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    client = authed_client(settings)
    mnt = tmp_path / "mw-mnt"
    dest = mnt / "mosaicWave"
    dest.mkdir(parents=True)
    captured: dict[str, object] = {}

    def fake_coerce(raw: str, **kwargs: object) -> Path:
        captured["raw"] = raw
        captured["user"] = kwargs.get("user")
        captured["password"] = kwargs.get("password")
        captured["mount_point"] = kwargs.get("mount_point")
        return dest

    monkeypatch.setattr("mosaicwave.api.v1.settings.coerce_host_folder", fake_coerce)
    monkeypatch.setattr("mosaicwave.api.v1.settings.share_mount_dest", lambda: str(mnt))
    res = client.put(
        "/api/v1/settings",
        json={
            "data_dir": "smb://fileserver/Public/mosaicWave",
            "smb_user": "nasuser",
            "smb_password": "secret",
            "mount_point": str(mnt),
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert captured["raw"] == "smb://fileserver/Public/mosaicWave"
    assert captured["user"] == "nasuser"
    assert captured["password"] == "secret"
    assert captured["mount_point"] == str(mnt)
    assert body["data_dir_smb"] == "smb://fileserver/Public/mosaicWave"
    assert body["data_dir_mount"] == str(mnt)
    assert Path(body["data_dir"]) == dest.resolve()
    cfg = json.loads((settings.bootstrap_dir / "config.json").read_text(encoding="utf-8"))
    assert cfg["data_dir_smb"] == "smb://fileserver/Public/mosaicWave"
    assert cfg["data_dir_mount"] == str(mnt)
    dumped = json.dumps(cfg)
    assert "secret" not in dumped
    assert "password" not in dumped
    assert (settings.bootstrap_dir / "library.db").is_file()
    assert not (dest / "library.db").exists()


def test_put_settings_surfaces_mount_fallback_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When Save folder lands on a numbered fallback dest (primary already in use by
    another mosaicWave instance), the FYI notice reaches the client."""
    settings = _settings(tmp_path)
    client = authed_client(settings)
    mnt = tmp_path / "mw-mnt"
    dest = mnt / "mosaicWave"
    dest.mkdir(parents=True)
    notice_text = (
        "/Volumes/mosaicWave-fileserver-Public was already in use by another mosaicWave "
        "instance; mounted at /Volumes/mosaicWave-fileserver-Public-2 instead."
    )

    monkeypatch.setattr("mosaicwave.api.v1.settings.coerce_host_folder", lambda *_a, **_k: dest)
    monkeypatch.setattr("mosaicwave.api.v1.settings.share_mount_dest", lambda: str(mnt))
    monkeypatch.setattr("mosaicwave.api.v1.settings.last_mount_notice", lambda: notice_text)
    res = client.put(
        "/api/v1/settings",
        json={
            "data_dir": "smb://fileserver/Public/mosaicWave",
            "smb_user": "nasuser",
            "smb_password": "secret",
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["notice"] == notice_text


def test_get_settings_never_surfaces_a_notice(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    admin = authed_client(settings)
    res = admin.get("/api/v1/settings")
    assert res.status_code == 200, res.text
    assert res.json()["notice"] is None
