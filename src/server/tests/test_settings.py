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
from test_cancel import _until


def _jpeg_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (8, 8), (40, 120, 200)).save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def _maintenance_on(client: TestClient) -> None:
    """Moving the data folder needs maintenance mode (milestone 13)."""
    res = client.put("/api/v1/maintenance", json={"enabled": True, "reason": "moving"})
    assert res.status_code == 200, res.text


def _move(client: TestClient, payload: dict) -> dict:
    """Start Move library and wait for the job. Returns the finished job."""
    res = client.put("/api/v1/settings", json=payload)
    assert res.status_code == 200, res.text
    assert res.json()["kind"] == "data_dir_move"
    return _until(client, res.json()["id"], {"done", "error", "cancelled"})


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
    _maintenance_on(client)
    lib = library_dir(settings, client)
    (lib / "kept.jpg").write_bytes(_jpeg_bytes())
    scan_library(client)
    listed = client.get("/api/v1/assets").json()["items"]
    assert len(listed) == 1
    asset_id = listed[0]["id"]

    dest = tmp_path / "moved"
    job = _move(client, {"data_dir": str(dest)})
    assert job["status"] == "done", job
    assert job["restarting"] is False
    now = client.get("/api/v1/settings").json()
    assert Path(now["data_dir"]) == dest.resolve()
    assert now.get("data_dir_smb") in (None, "")
    assert now.get("data_dir_mount") in (None, "")
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
    _maintenance_on(client)
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
    _maintenance_on(client)

    def boom(_src: Path, _dest: Path, **_kwargs: object) -> None:
        raise OSError("NAS chmod")

    monkeypatch.setattr("mosaicwave.runtime.copy_data_dir", boom)
    dest = tmp_path / "nas"
    src_db = settings.bootstrap_dir / "library.db"
    assert src_db.is_file()
    job = _move(client, {"data_dir": str(dest)})
    assert job["status"] == "error" and "NAS chmod" in job["error"], job
    assert client.app.state.moving is False
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
    # Without maintenance mode nothing moves (milestone 13), not even a save of the same folder.
    gated = client.put("/api/v1/settings", json={"data_dir": str(settings.data_dir.resolve())})
    assert gated.status_code == 409 and gated.json()["code"] == "maintenance_required"
    _maintenance_on(client)
    # same path is a no-op relocate and writes config.json
    same = _move(client, {"data_dir": str(settings.data_dir.resolve())})
    assert same["status"] == "done", same
    assert read_data_dir_config(settings.bootstrap_dir) == settings.data_dir.resolve()


def test_put_settings_returns_to_app_folder(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = authed_client(settings)
    _maintenance_on(client)
    dest = tmp_path / "moved"
    assert _move(client, {"data_dir": str(dest)})["status"] == "done"
    assert (settings.bootstrap_dir / "library.db").is_file()
    (settings.bootstrap_dir / "libraries").mkdir(exist_ok=True)
    (settings.bootstrap_dir / "libraries" / "old.txt").write_text("stale", encoding="utf-8")
    home = _move(client, {"data_dir": str(settings.bootstrap_dir)})
    assert home["status"] == "done", home
    body = client.get("/api/v1/settings").json()
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
    _maintenance_on(client)
    dest = tmp_path / "emptyish"
    dest.mkdir()
    sqlite3.connect(dest / "library.db").close()
    (dest / "libraries").mkdir()
    job = _move(client, {"data_dir": str(dest)})
    assert job["status"] == "done", job
    assert Path(client.get("/api/v1/settings").json()["data_dir"]) == dest.resolve()
    assert (settings.bootstrap_dir / "library.db").is_file()


def test_put_settings_keeps_smb_source_and_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    client = authed_client(settings)
    _maintenance_on(client)
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
    job = _move(
        client,
        {
            "data_dir": "smb://fileserver/Public/mosaicWave",
            "smb_user": "nasuser",
            "smb_password": "secret",
            "mount_point": str(mnt),
        },
    )
    assert job["status"] == "done", job
    assert job["restarting"] is False
    body = client.get("/api/v1/settings").json()
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
    _maintenance_on(client)
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
    job = _move(
        client,
        {
            "data_dir": "smb://fileserver/Public/mosaicWave",
            "smb_user": "nasuser",
            "smb_password": "secret",
        },
    )
    assert job["status"] == "done", job
    assert job["notice"] == notice_text


def test_get_settings_never_surfaces_a_notice(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    admin = authed_client(settings)
    res = admin.get("/api/v1/settings")
    assert res.status_code == 200, res.text
    assert res.json()["notice"] is None


def _app_folder_settings(tmp_path: Path) -> Settings:
    boot = tmp_path / "boot"
    return Settings(data_dir=boot, takeout_dir=None, source_dir=None, bootstrap_dir=boot)


def test_move_from_app_folder_carries_backups_exports_not_databases(tmp_path: Path) -> None:
    settings = _app_folder_settings(tmp_path)
    client = authed_client(settings)
    _maintenance_on(client)
    lib = library_dir(settings, client)
    (lib / "kept.jpg").write_bytes(_jpeg_bytes())
    scan_library(client)
    boot = settings.bootstrap_dir
    (boot / "backups").mkdir(exist_ok=True)
    (boot / "backups" / "site.zip").write_bytes(b"backup")
    (boot / "exports" / "1").mkdir(parents=True, exist_ok=True)
    (boot / "exports" / "1" / "e.zip").write_bytes(b"export")
    accounts = sorted(p.name for p in (boot / "accounts").iterdir())
    assert accounts

    dest = tmp_path / "mosaic" / "dev"
    job = _move(client, {"data_dir": str(dest)})
    assert job["status"] == "done", job
    assert sorted(p.name for p in (boot / "accounts").iterdir()) == accounts
    assert (boot / "library.db").is_file()
    assert not (boot / "backups").exists() and not (boot / "exports").exists()
    assert (dest / "backups" / "site.zip").read_bytes() == b"backup"
    assert (dest / "exports" / "1" / "e.zip").read_bytes() == b"export"
    assert not (dest / "accounts").exists() and not (dest / "library.db").exists()
    assert (dest / "libraries" / lib.name / "kept.jpg").is_file()
    assert len(client.get("/api/v1/assets").json()["items"]) == 1
    # The moved site backup is still listed and downloadable from the new place.
    assert client.get("/api/v1/settings").json()["backup_dir"] == str((dest / "backups").resolve())
    from mosaicwave.backup import backup_site

    live = client.app.state.settings
    made = backup_site(live, live.backup_dir).path
    assert made.parent == (dest / "backups").resolve()
    names = [b["name"] for b in client.get("/api/v1/backups").json()]
    assert names == [made.name]
    assert client.get(f"/api/v1/backups/{made.name}").status_code == 200


def test_move_refused_when_new_folder_lacks_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _app_folder_settings(tmp_path)
    client = authed_client(settings)
    _maintenance_on(client)
    boot = settings.bootstrap_dir
    (boot / "backups").mkdir(exist_ok=True)
    (boot / "backups" / "site.zip").write_bytes(b"x" * 4096)

    class Usage:
        total = used = 0
        free = 100

    monkeypatch.setattr("mosaicwave.config.shutil.disk_usage", lambda _p: Usage)
    dest = tmp_path / "small"
    res = client.put("/api/v1/settings", json={"data_dir": str(dest)})
    assert res.status_code == 400, res.text
    assert "not enough free space" in res.json()["detail"]
    assert (boot / "backups" / "site.zip").is_file()
    assert read_data_dir_config(boot) is None
    assert client.get("/api/v1/settings").status_code == 200
    assert not list(dest.rglob("*.zip"))


def test_legacy_backups_and_exports_are_adopted_into_the_data_folder(tmp_path: Path) -> None:
    from mosaicwave.config import adopt_legacy_data_folders

    settings = _settings(tmp_path)  # data folder differs from the app folder
    app, data = settings.bootstrap_dir, settings.data_dir
    (app / "backups").mkdir(parents=True)
    (app / "backups" / "a.zip").write_bytes(b"a")
    (app / "exports" / "u").mkdir(parents=True)
    (app / "exports" / "u" / "e.zip").write_bytes(b"e")
    (data / "exports" / "u").mkdir(parents=True)  # a non-empty target is never merged into
    (data / "exports" / "u" / "mine.zip").write_bytes(b"m")

    assert adopt_legacy_data_folders(settings) == ["backups"]
    assert (data / "backups" / "a.zip").is_file() and not (app / "backups").exists()
    assert (app / "exports" / "u" / "e.zip").is_file()
    assert (data / "exports" / "u" / "mine.zip").is_file()
    assert adopt_legacy_data_folders(settings) == []


def test_settings_while_library_detached_is_503(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = authed_client(settings)
    engine = client.app.state.engine
    client.app.state.engine = None
    try:
        res = client.get("/api/v1/settings")
    finally:
        client.app.state.engine = engine
    assert res.status_code == 503, res.text
    assert res.json()["code"] == "data_dir_moving"


def test_failed_move_cleanup_error_still_reattaches_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    client = authed_client(settings)
    _maintenance_on(client)

    def boom(_src: Path, _dest: Path, **kwargs: object) -> None:
        kwargs["before_rewrite"]()  # type: ignore[operator]  # the databases are detached here
        raise ValueError("disk full")

    monkeypatch.setattr("mosaicwave.runtime.copy_data_dir", boom)
    monkeypatch.setattr(
        "mosaicwave.runtime._rewrite_library_root_uris",
        lambda *a, **k: (_ for _ in ()).throw(OSError("locked")),
    )
    job = _move(client, {"data_dir": str(tmp_path / "nas")})
    assert job["status"] == "error" and "disk full" in job["error"], job
    assert client.get("/api/v1/settings").status_code == 200
    assert client.get("/api/v1/assets").status_code == 200  # the old library is attached again


def test_library_requests_wait_while_the_data_folder_is_moving(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = authed_client(settings)
    lib = library_dir(settings, client)
    (lib / "kept.jpg").write_bytes(_jpeg_bytes())
    scan_library(client)
    client.app.state.moving = True
    try:
        for path in ("/api/v1/assets", "/api/v1/albums", "/api/v1/users"):
            res = client.get(path)
            assert res.status_code == 503, (path, res.text)
            assert res.json()["code"] == "data_dir_moving"
            assert res.headers["Retry-After"] == "5"
        # What the admin needs to follow the move still answers.
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/v1/me").status_code == 200
        assert client.get("/api/v1/settings").status_code == 200
        assert client.get("/api/v1/jobs/running").status_code == 200
        assert client.get("/api/v1/maintenance").status_code == 200
    finally:
        client.app.state.moving = False
    assert client.get("/api/v1/assets").status_code == 200


def test_put_settings_marks_the_move_and_clears_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    client = authed_client(settings)
    _maintenance_on(client)
    seen: list[bool] = []

    def fake_switch(app, dest, **_kwargs):  # type: ignore[no-untyped-def]
        seen.append(app.state.moving)
        raise ValueError("stop here")

    monkeypatch.setattr("mosaicwave.api.v1.settings.switch_data_dir", fake_switch)
    job = _move(client, {"data_dir": str(tmp_path / "elsewhere")})
    assert job["status"] == "error" and "stop here" in job["error"], job
    assert seen == [True]
    assert client.app.state.moving is False
    assert client.get("/api/v1/assets").status_code == 200
