from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from mosaicwave.config import (
    Settings,
    _default_data_dir,
    _platform_bootstrap_dir,
    ensure_writable_dir,
    normalize_host_path_text,
    relocate_data_dir,
    resolve_data_dir,
    copy_data_dir,
    data_dir_occupied,
    read_data_dir_config,
    read_data_dir_smb,
    read_data_dir_mount,
    write_data_dir_config,
)


def _clear_data_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MOSAICWAVE_DATA_DIR", raising=False)
    monkeypatch.delenv("MOSAICWAVE_PROFILE", raising=False)


@pytest.mark.skipif(os.name != "nt", reason="Windows data dir")
def test_default_data_dir_windows_dev_programdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    prog = tmp_path / "ProgramData"
    local = tmp_path / "Local"
    local.mkdir()
    monkeypatch.setenv("PROGRAMDATA", str(prog))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    _clear_data_env(monkeypatch)
    assert _default_data_dir() == prog / "mosaicWave-dev"
    settings = Settings.from_env()
    assert settings.data_dir == prog / "mosaicWave-dev"
    assert settings.blob_dir == prog / "mosaicWave-dev" / "blobs"


@pytest.mark.skipif(os.name != "nt", reason="Windows data dir")
def test_prod_profile_uses_mosaicwave(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    prog = tmp_path / "ProgramData"
    local = tmp_path / "Local"
    local.mkdir()
    monkeypatch.setenv("PROGRAMDATA", str(prog))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.delenv("MOSAICWAVE_DATA_DIR", raising=False)
    monkeypatch.setenv("MOSAICWAVE_PROFILE", "prod")
    assert _default_data_dir() == prog / "mosaicWave"


@pytest.mark.skipif(os.name != "nt", reason="Windows data dir")
def test_relocates_legacy_localappdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    prog = tmp_path / "ProgramData"
    local = tmp_path / "Local"
    legacy = local / "mosaicWave"
    lib = legacy / "libraries" / "user-1"
    lib.mkdir(parents=True)
    (lib / "shot.jpg").write_bytes(b"jpeg")
    (legacy / "blobs").mkdir()
    (legacy / "blobs" / "thumb.bin").write_bytes(b"t")
    db = legacy / "library.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE source (id TEXT PRIMARY KEY, root_uri TEXT)")
    con.execute(
        "INSERT INTO source (id, root_uri) VALUES (?, ?)",
        ("src-1", str(lib.resolve())),
    )
    con.commit()
    con.close()
    monkeypatch.setenv("PROGRAMDATA", str(prog))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    _clear_data_env(monkeypatch)
    dest = _default_data_dir()
    assert dest == prog / "mosaicWave-dev"
    assert (dest / "library.db").is_file()
    assert (dest / "libraries" / "user-1" / "shot.jpg").is_file()
    assert (dest / "blobs" / "thumb.bin").is_file()
    assert not db.exists()
    con = sqlite3.connect(dest / "library.db")
    uri = con.execute("SELECT root_uri FROM source WHERE id = 'src-1'").fetchone()[0]
    con.close()
    assert Path(uri) == (dest / "libraries" / "user-1").resolve()


def test_relocates_macos_legacy_user_library(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    legacy = home / "Library" / "Application Support" / "mosaicWave"
    lib = legacy / "libraries" / "user-1"
    lib.mkdir(parents=True)
    (lib / "shot.jpg").write_bytes(b"jpeg")
    db = legacy / "library.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE source (id TEXT PRIMARY KEY, root_uri TEXT)")
    con.execute(
        "INSERT INTO source (id, root_uri) VALUES (?, ?)",
        ("src-1", str(lib.resolve())),
    )
    con.commit()
    con.close()
    monkeypatch.setattr("mosaicwave.config.os.name", "posix")
    monkeypatch.setattr("mosaicwave.config.sys.platform", "darwin")
    monkeypatch.setattr("mosaicwave.config._installed_macos_payload", lambda: False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    dest_boot = tmp_path / "Library" / "Application Support" / "mosaicWave-dev"
    monkeypatch.setattr("mosaicwave.config._macos_dev_bootstrap", lambda: dest_boot)
    _clear_data_env(monkeypatch)
    dest = _default_data_dir()
    assert dest == dest_boot
    assert (dest / "library.db").is_file()
    assert (dest / "libraries" / "user-1" / "shot.jpg").is_file()
    assert not db.exists()
    con = sqlite3.connect(dest / "library.db")
    uri = con.execute("SELECT root_uri FROM source WHERE id = 'src-1'").fetchone()[0]
    con.close()
    assert Path(uri) == (dest / "libraries" / "user-1").resolve()


def test_config_json_sets_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    prog = tmp_path / "ProgramData"
    local = tmp_path / "Local"
    local.mkdir()
    custom = tmp_path / "custom"
    custom.mkdir()
    monkeypatch.setenv("PROGRAMDATA", str(prog))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("XDG_DATA_HOME", str(prog))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(
        "mosaicwave.config._macos_dev_bootstrap",
        lambda: tmp_path / "Library" / "Application Support" / "mosaicWave-dev",
    )
    _clear_data_env(monkeypatch)
    write_data_dir_config(_platform_bootstrap_dir(), custom)
    assert resolve_data_dir() == custom.resolve()
    monkeypatch.setenv("MOSAICWAVE_DATA_DIR", str(tmp_path / "from-env"))
    assert resolve_data_dir() == (tmp_path / "from-env").resolve()


def test_relocate_leaves_bootstrap_config(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    (src / "library.db").write_bytes(b"")
    write_data_dir_config(src, dest)
    relocate_data_dir(src, dest)
    assert (src / "config.json").is_file()
    assert not (dest / "config.json").exists()
    assert (dest / "library.db").is_file()


def test_relocate_skips_runtime(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    (src / "library.db").write_bytes(b"")
    (src / "runtime" / "venv").mkdir(parents=True)
    (src / "runtime" / "venv" / "py").write_bytes(b"x")
    (src / "mosaicWave.log").write_text("log", encoding="utf-8")
    (src / "session.key").write_text("old-key", encoding="utf-8")
    relocate_data_dir(src, dest)
    assert (dest / "library.db").is_file()
    assert (src / "runtime" / "venv" / "py").is_file()
    assert (src / "mosaicWave.log").is_file()
    assert (src / "session.key").is_file()
    assert not (dest / "runtime").exists()
    assert not (dest / "mosaicWave.log").exists()
    assert not (dest / "session.key").exists()


def test_relocate_rejects_nested_data_dir(tmp_path: Path) -> None:
    src = tmp_path / "mosaicWave-dev"
    dest = src / "mnt" / "mosaicWave-fileserver-Public"
    src.mkdir()
    dest.mkdir(parents=True)
    (src / "library.db").write_bytes(b"db")
    with pytest.raises(ValueError, match="inside the current data folder"):
        relocate_data_dir(src, dest)


def test_relocate_skips_leftover_mnt(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    sqlite3.connect(src / "library.db").close()
    (src / "mnt" / "leftover").mkdir(parents=True)
    (src / "mnt" / "leftover" / "x").write_bytes(b"x")
    relocate_data_dir(src, dest)
    assert (dest / "library.db").is_file()
    assert (src / "mnt" / "leftover" / "x").is_file()
    assert not (dest / "mnt").exists()


def test_relocate_skips_tmp(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    sqlite3.connect(src / "library.db").close()
    (src / "tmp" / "takeout-push" / "job").mkdir(parents=True)
    (src / "tmp" / "takeout-push" / "job" / "a.jpg").write_bytes(b"x")
    relocate_data_dir(src, dest)
    assert (dest / "library.db").is_file()
    assert (src / "tmp" / "takeout-push" / "job" / "a.jpg").is_file()
    assert not (dest / "tmp").exists()


def test_copy_ignores_finder_ds_store(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    sqlite3.connect(src / "library.db").close()
    (src / ".DS_Store").write_bytes(b"from-src")
    (dest / ".DS_Store").write_bytes(b"from-dest")
    copy_data_dir(src, dest)
    assert not (dest / "library.db").exists()
    assert (src / "library.db").is_file()
    assert (dest / ".DS_Store").read_bytes() == b"from-dest"


def test_copy_skips_wal_sidecars(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    db = src / "library.db"
    con = sqlite3.connect(db)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE source (id TEXT PRIMARY KEY, root_uri TEXT)")
    con.execute(
        "INSERT INTO source (id, root_uri) VALUES (?, ?)",
        ("s1", str((src / "libraries").resolve())),
    )
    con.commit()
    con.close()
    (src / "library.db-wal").write_bytes(b"not-a-sidecar-if-missing")
    (dest / ".DS_Store").write_bytes(b"x")
    copy_data_dir(src, dest)
    assert not (dest / "library.db").exists()
    assert not (dest / "library.db-wal").exists()
    con = sqlite3.connect(src / "library.db")
    uri = con.execute("SELECT root_uri FROM source WHERE id = 's1'").fetchone()[0]
    con.close()
    assert Path(uri) == (dest / "libraries").resolve()


def test_copy_rewrites_db_without_opening_dest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    con = sqlite3.connect(src / "library.db")
    con.execute("CREATE TABLE source (id TEXT PRIMARY KEY, root_uri TEXT)")
    con.execute(
        "INSERT INTO source (id, root_uri) VALUES (?, ?)",
        ("s1", str((src / "libraries").resolve())),
    )
    con.commit()
    con.close()
    dest_root = dest.resolve()
    orig = sqlite3.connect

    def guarded(database: object, *args: object, **kwargs: object) -> sqlite3.Connection:
        if isinstance(database, int):
            return orig(database, *args, **kwargs)
        text = os.fsdecode(database) if isinstance(database, bytes) else os.fspath(database)
        if text.startswith("file:") or "?" in text:
            return orig(database, *args, **kwargs)
        path = Path(text).resolve()
        try:
            path.relative_to(dest_root)
        except ValueError:
            return orig(database, *args, **kwargs)
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(sqlite3, "connect", guarded)
    copy_data_dir(src, dest)
    monkeypatch.setattr(sqlite3, "connect", orig)
    con = sqlite3.connect(src / "library.db")
    uri = con.execute("SELECT root_uri FROM source WHERE id = 's1'").fetchone()[0]
    con.close()
    assert Path(uri) == (dest / "libraries").resolve()
    assert not (dest / "library.db").exists()


def _force_cross_device(monkeypatch: pytest.MonkeyPatch, dest: Path) -> None:
    orig_stat = os.stat
    dest_abs = os.path.normcase(os.path.abspath(str(dest.resolve())))
    sep = os.sep

    def fake_stat(path: str | os.PathLike[str], *args: object, **kwargs: object) -> os.stat_result:
        st = orig_stat(path, *args, **kwargs)
        try:
            raw = os.path.normcase(os.path.abspath(os.fsdecode(os.fspath(path))))
        except (TypeError, ValueError, OSError):
            return st
        if raw != dest_abs and not raw.startswith(dest_abs + sep):
            return st

        class _Dev:
            def __init__(self, inner: os.stat_result) -> None:
                object.__setattr__(self, "_inner", inner)
                object.__setattr__(self, "st_dev", inner.st_dev + 1)

            def __getattr__(self, name: str) -> object:
                return getattr(self._inner, name)

        return _Dev(st)  # type: ignore[return-value]

    monkeypatch.setattr(os, "stat", fake_stat)


def test_relocate_cross_device_ignores_chmod(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    lib = src / "libraries" / "u1"
    lib.mkdir(parents=True)
    (lib / "a.jpg").write_bytes(b"jpeg")
    (src / "library.db").write_bytes(b"")
    _force_cross_device(monkeypatch, dest)

    def boom_chmod(*_a: object, **_k: object) -> None:
        raise PermissionError(13, "Operation not permitted")

    monkeypatch.setattr(os, "chmod", boom_chmod)
    relocate_data_dir(src, dest)
    assert (dest / "libraries" / "u1" / "a.jpg").read_bytes() == b"jpeg"
    assert (dest / "library.db").is_file()
    assert not (src / "library.db").exists()
    assert not (src / "libraries").exists()


def test_relocate_cross_device_rollback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    (src / "library.db").write_bytes(b"db")
    (src / "libraries").mkdir()
    (src / "libraries" / "kept.jpg").write_bytes(b"jpeg")
    _force_cross_device(monkeypatch, dest)
    import mosaicwave.config as mwconfig

    orig = mwconfig._copy_path_bytes

    def flaky(src_path: Path, dest_path: Path) -> None:
        if src_path.name == "libraries":
            dest_path.mkdir(parents=True, exist_ok=True)
            (dest_path / "leftover.jpg").write_bytes(b"x")
            raise OSError("NAS")
        orig(src_path, dest_path)

    monkeypatch.setattr(mwconfig, "_copy_path_bytes", flaky)
    with pytest.raises(ValueError, match="could not copy"):
        relocate_data_dir(src, dest)
    assert (src / "library.db").is_file()
    assert (src / "libraries" / "kept.jpg").is_file()
    assert not (dest / "libraries").exists()
    assert not (dest / "library.db").exists()


def test_macos_bootstrap_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("MOSAICWAVE_DATA_DIR", raising=False)
    monkeypatch.delenv("MOSAICWAVE_PROFILE", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr("mosaicwave.config.os.name", "posix")
    monkeypatch.setattr("mosaicwave.config.sys.platform", "darwin")
    monkeypatch.setattr("mosaicwave.config._installed_macos_payload", lambda: False)
    assert _platform_bootstrap_dir() == Path("/Library/Application Support/mosaicWave-dev")


def test_macos_dev_profile_bootstrap_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("MOSAICWAVE_DATA_DIR", raising=False)
    monkeypatch.setenv("MOSAICWAVE_PROFILE", "dev")
    monkeypatch.setattr("mosaicwave.config.os.name", "posix")
    monkeypatch.setattr("mosaicwave.config.sys.platform", "darwin")
    monkeypatch.setattr("mosaicwave.config._installed_macos_payload", lambda: False)
    assert _platform_bootstrap_dir() == Path("/Library/Application Support/mosaicWave-dev")


def test_macos_prod_bootstrap_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MOSAICWAVE_DATA_DIR", raising=False)
    monkeypatch.setattr("mosaicwave.config.os.name", "posix")
    monkeypatch.setattr("mosaicwave.config.sys.platform", "darwin")
    monkeypatch.setenv("MOSAICWAVE_PROFILE", "prod")
    assert _platform_bootstrap_dir() == Path("/Library/Application Support/mosaicWave")


def test_macos_installed_payload_uses_library_support(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MOSAICWAVE_DATA_DIR", raising=False)
    monkeypatch.delenv("MOSAICWAVE_PROFILE", raising=False)
    monkeypatch.setattr("mosaicwave.config.os.name", "posix")
    monkeypatch.setattr("mosaicwave.config.sys.platform", "darwin")
    monkeypatch.setattr("mosaicwave.config._installed_macos_payload", lambda: True)
    assert _platform_bootstrap_dir() == Path("/Library/Application Support/mosaicWave")


def test_linux_bootstrap_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear_data_env(monkeypatch)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr("mosaicwave.config.os.name", "posix")
    monkeypatch.setattr("mosaicwave.config.sys.platform", "linux")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    assert _platform_bootstrap_dir() == tmp_path / ".local" / "share" / "mosaicWave"


def test_linux_dev_bootstrap_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("MOSAICWAVE_DATA_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("MOSAICWAVE_PROFILE", "dev")
    monkeypatch.setattr("mosaicwave.config.os.name", "posix")
    monkeypatch.setattr("mosaicwave.config.sys.platform", "linux")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    assert _platform_bootstrap_dir() == tmp_path / ".local" / "share" / "mosaicWave-dev"


def test_normalize_unc_on_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mosaicwave.config.os.name", "posix")
    monkeypatch.setattr("mosaicwave.storage.smbpath.os.name", "posix")
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "linux")
    assert (
        normalize_host_path_text(r"\\fileserver\Public\mosaicWave")
        == "//fileserver/Public/mosaicWave"
    )


def test_from_env_finds_bundled_web(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    install = tmp_path / "app"
    pkg = install / "server" / "mosaicwave"
    pkg.mkdir(parents=True)
    (install / "web").mkdir()
    (install / "web" / "index.html").write_text("<html>mw</html>", encoding="utf-8")
    monkeypatch.setattr("mosaicwave.config.__file__", str(pkg / "config.py"))
    monkeypatch.delenv("MOSAICWAVE_WEB_ROOT", raising=False)
    monkeypatch.setenv("MOSAICWAVE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "ProgramData"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    got = Settings.from_env()
    assert got.web_root == (install / "web").resolve()


def test_data_dir_occupied_ignores_app_files(tmp_path: Path) -> None:
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "session.key").write_text("k", encoding="utf-8")
    (boot / "config.json").write_text("{}", encoding="utf-8")
    (boot / "runtime").mkdir()
    sqlite3.connect(boot / "library.db").close()
    assert not data_dir_occupied(boot)


def test_data_dir_occupied_real_library(tmp_path: Path) -> None:
    dest = tmp_path / "taken"
    dest.mkdir()
    con = sqlite3.connect(dest / "library.db")
    con.execute("CREATE TABLE user (id TEXT)")
    con.execute("INSERT INTO user (id) VALUES ('u1')")
    con.commit()
    con.close()
    assert not data_dir_occupied(dest)
    (dest / "libraries").mkdir()
    (dest / "libraries" / "shot.jpg").write_bytes(b"jpeg")
    assert data_dir_occupied(dest)


def test_copy_replace_payload_overwrites_leftover_library(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    con = sqlite3.connect(src / "library.db")
    con.execute("CREATE TABLE source (id TEXT PRIMARY KEY, root_uri TEXT)")
    con.execute(
        "INSERT INTO source (id, root_uri) VALUES (?, ?)",
        ("s1", str((src / "libraries").resolve())),
    )
    con.commit()
    con.close()
    leftover = sqlite3.connect(dest / "library.db")
    leftover.execute("CREATE TABLE user (id TEXT)")
    leftover.execute("INSERT INTO user (id) VALUES ('stale')")
    leftover.commit()
    leftover.close()
    (src / "libraries").mkdir()
    (src / "libraries" / "a.jpg").write_bytes(b"jpeg")
    (dest / "session.key").write_text("keep", encoding="utf-8")
    copy_data_dir(src, dest, replace_payload=True)
    assert (dest / "session.key").read_text(encoding="utf-8") == "keep"
    assert (dest / "libraries" / "a.jpg").is_file()
    con = sqlite3.connect(src / "library.db")
    assert con.execute("SELECT id FROM source").fetchone()[0] == "s1"
    con.close()
    leftover = sqlite3.connect(dest / "library.db")
    assert leftover.execute("SELECT id FROM user").fetchone()[0] == "stale"
    leftover.close()


def test_copy_rejects_real_library_without_replace(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    sqlite3.connect(src / "library.db").close()
    (src / "libraries").mkdir()
    (src / "libraries" / "a.jpg").write_bytes(b"jpeg")
    (dest / "libraries").mkdir()
    (dest / "libraries" / "stale.jpg").write_bytes(b"x")
    with pytest.raises(ValueError, match="already contains"):
        copy_data_dir(src, dest)


def test_write_config_records_and_clears_smb(tmp_path: Path) -> None:
    boot = tmp_path / "boot"
    dest = tmp_path / "nas"
    dest.mkdir()
    write_data_dir_config(
        boot, dest, smb_url="smb://fileserver/Public/mosaicWave", mount_point="/Volumes/mosaicWave-fileserver-Public"
    )
    data = json.loads((boot / "config.json").read_text(encoding="utf-8"))
    assert data["data_dir_smb"] == "smb://fileserver/Public/mosaicWave"
    assert data["data_dir_mount"] == "/Volumes/mosaicWave-fileserver-Public"
    assert read_data_dir_config(boot) == dest.resolve()
    assert read_data_dir_smb(boot) == "smb://fileserver/Public/mosaicWave"
    assert read_data_dir_mount(boot) == "/Volumes/mosaicWave-fileserver-Public"
    local = tmp_path / "local"
    local.mkdir()
    write_data_dir_config(boot, local)
    data = json.loads((boot / "config.json").read_text(encoding="utf-8"))
    assert "data_dir_smb" not in data
    assert "data_dir_mount" not in data
    assert read_data_dir_smb(boot) is None
    assert read_data_dir_mount(boot) is None


def test_ensure_writable_dir_non_dot_probe(tmp_path: Path) -> None:
    folder = tmp_path / "mosaicWave"
    ensure_writable_dir(folder)
    assert folder.is_dir()
    assert not (folder / ".mosaicwave-write-test").exists()
    assert not (folder / "mosaicwave-write-test.tmp").exists()


def test_resolve_data_dir_remounts_saved_smb(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    boot = tmp_path / "boot"
    mount_dest = tmp_path / "mw-mnt"
    library = mount_dest / "mosaicWave"
    library.mkdir(parents=True)
    write_data_dir_config(
        boot,
        library,
        smb_url="smb://fileserver/Public/mosaicWave",
        mount_point=str(mount_dest),
    )
    mounted = tmp_path / "mosaicWave-fileserver-Public" / "mosaicWave"
    mounted.mkdir(parents=True)
    seen: dict[str, str] = {}

    def coerce(raw: str, mount: bool = True, **kwargs: object) -> Path:
        del mount
        seen["raw"] = raw
        seen["mount_point"] = str(kwargs.get("mount_point") or "")
        return mounted

    monkeypatch.setenv("MOSAICWAVE_SMB_MOUNT_ATTEMPTS", "1")
    monkeypatch.setenv("MOSAICWAVE_SMB_MOUNT_DELAY", "0")
    monkeypatch.delenv("MOSAICWAVE_DATA_DIR", raising=False)
    monkeypatch.setattr("mosaicwave.storage.smbpath.coerce_host_folder", coerce)
    monkeypatch.setattr(
        "mosaicwave.storage.smbpath.refuse_unmounted_volumes_path",
        lambda _p: None,
    )
    assert resolve_data_dir(bootstrap=boot) == mounted
    assert seen["raw"] == "smb://fileserver/Public/mosaicWave"
    assert seen["mount_point"] == str(mount_dest)


def test_resolve_data_dir_remounts_leftover_smb_on_app_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    boot = tmp_path / "boot"
    boot.mkdir()
    mounted = tmp_path / "mosaicWave-fileserver-Public" / "mosaicWave"
    mounted.mkdir(parents=True)
    write_data_dir_config(
        boot,
        boot,
        smb_url="smb://fileserver/Public/mosaicWave",
        mount_point="/Volumes/mosaicWave-fileserver-Public",
    )
    seen: dict[str, str] = {}

    def coerce(raw: str, mount: bool = True, **kwargs: object) -> Path:
        del mount
        seen["raw"] = raw
        seen["mount_point"] = str(kwargs.get("mount_point") or "")
        return mounted

    monkeypatch.setenv("MOSAICWAVE_SMB_MOUNT_ATTEMPTS", "1")
    monkeypatch.setenv("MOSAICWAVE_SMB_MOUNT_DELAY", "0")
    monkeypatch.delenv("MOSAICWAVE_DATA_DIR", raising=False)
    monkeypatch.setattr("mosaicwave.storage.smbpath.coerce_host_folder", coerce)
    monkeypatch.setattr(
        "mosaicwave.storage.smbpath.refuse_unmounted_volumes_path",
        lambda _p: None,
    )
    assert resolve_data_dir(bootstrap=boot) == mounted
    assert seen["raw"] == "smb://fileserver/Public/mosaicWave"
    assert seen["mount_point"] == "/Volumes/mosaicWave-fileserver-Public"


def test_resolve_data_dir_smb_unmounted_keeps_pointer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    boot = tmp_path / "boot"
    boot.mkdir()
    mount_dest = tmp_path / "mw-mnt"
    library = mount_dest / "mosaicWave"
    library.mkdir(parents=True)
    write_data_dir_config(
        boot,
        library,
        smb_url="smb://fileserver/Public/mosaicWave",
        mount_point=str(mount_dest),
    )
    monkeypatch.setenv("MOSAICWAVE_SMB_MOUNT_ATTEMPTS", "1")
    monkeypatch.setenv("MOSAICWAVE_SMB_MOUNT_DELAY", "0")
    monkeypatch.delenv("MOSAICWAVE_DATA_DIR", raising=False)

    def boom(*_a: object, **_k: object) -> Path:
        raise ValueError("could not mount")

    monkeypatch.setattr("mosaicwave.storage.smbpath.coerce_host_folder", boom)
    with pytest.raises(ValueError, match="could not mount"):
        resolve_data_dir(bootstrap=boot)
    assert read_data_dir_smb(boot) == "smb://fileserver/Public/mosaicWave"


def test_schedule_api_restart_skipped_under_pytest(tmp_path: Path) -> None:
    from mosaicwave.config import restart_flag_path, schedule_api_restart

    boot = tmp_path / "boot"
    boot.mkdir()
    assert schedule_api_restart(boot) is False
    assert not restart_flag_path(boot).is_file()


def test_clear_bootstrap_library_payload(tmp_path: Path) -> None:
    from mosaicwave.config import clear_bootstrap_library_payload

    boot = tmp_path / "boot"
    dest = tmp_path / "nas"
    boot.mkdir()
    dest.mkdir()
    (boot / "library.db").write_bytes(b"src")
    (boot / "blobs").mkdir()
    (boot / "blobs" / "t.bin").write_bytes(b"t")
    clear_bootstrap_library_payload(boot, dest)
    assert (boot / "library.db").is_file()
    (dest / "library.db").write_bytes(b"dst")
    clear_bootstrap_library_payload(boot, dest)
    assert (boot / "library.db").is_file()
    assert not (boot / "blobs").exists()
    assert (dest / "library.db").is_file()


def test_resolve_data_dir_env_smb(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mounted = tmp_path / "m"
    mounted.mkdir()
    monkeypatch.setenv("MOSAICWAVE_DATA_DIR", "smb://fileserver/Public/mosaicWave")
    monkeypatch.setenv("MOSAICWAVE_SMB_MOUNT_ATTEMPTS", "1")
    monkeypatch.setenv("MOSAICWAVE_SMB_MOUNT_DELAY", "0")
    monkeypatch.setattr(
        "mosaicwave.storage.smbpath.coerce_host_folder",
        lambda raw, mount=True, **_k: mounted,
    )
    monkeypatch.setattr(
        "mosaicwave.storage.smbpath.refuse_unmounted_volumes_path",
        lambda _p: None,
    )
    assert resolve_data_dir() == mounted


def test_database_path_stays_at_bootstrap(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "nas",
        takeout_dir=None,
        source_dir=None,
        bootstrap_dir=tmp_path / "boot",
    )
    assert settings.database_path == tmp_path / "boot" / "library.db"
    assert settings.blob_dir == tmp_path / "nas" / "blobs"


def test_database_path_follows_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MOSAICWAVE_DATA_DIR", str(tmp_path / "env"))
    settings = Settings(
        data_dir=tmp_path / "env",
        takeout_dir=None,
        source_dir=None,
        bootstrap_dir=tmp_path / "boot",
    )
    assert settings.database_path == tmp_path / "env" / "library.db"


def test_recover_library_db_to_bootstrap(tmp_path: Path) -> None:
    from mosaicwave.config import recover_library_db_to_bootstrap

    boot = tmp_path / "boot"
    nas = tmp_path / "nas"
    boot.mkdir()
    nas.mkdir()
    con = sqlite3.connect(nas / "library.db")
    con.execute("CREATE TABLE user (id TEXT)")
    con.execute("INSERT INTO user (id) VALUES ('u1')")
    con.commit()
    con.close()
    recover_library_db_to_bootstrap(boot, nas)
    assert (boot / "library.db").is_file()
    con = sqlite3.connect(boot / "library.db")
    assert con.execute("SELECT id FROM user").fetchone()[0] == "u1"
    con.close()
    recover_library_db_to_bootstrap(boot, nas)
    con = sqlite3.connect(boot / "library.db")
    assert con.execute("SELECT COUNT(*) FROM user").fetchone()[0] == 1
    con.close()
