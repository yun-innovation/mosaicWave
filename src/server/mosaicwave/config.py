from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import sqlite3
import sys
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path

_log = logging.getLogger(__name__)

_CONFIG_NAME = "config.json"
# Host process files stay at bootstrap. Moving a venv onto SMB/AFP (macOS NAS)
# fails mid-copy (chmod / symlinks) and leaves a partial tree.
_SKIP_RELOCATE = {
    _CONFIG_NAME,
    "config.json.tmp",
    "runtime",
    "mosaicWave.pid",
    "mosaicWave.log",
    "web.port",
    "session.key",
    "mnt",
    "restart.flag",
    "nsmb.conf",
    "tmp",
}
_SKIP_COPY_NAMES = {
    ".DS_Store",
    ".localized",
    "Thumbs.db",
    "desktop.ini",
    "library.db-wal",
    "library.db-shm",
    "library.db-journal",
}
_SQLITE_FILE_NAMES = {"library.db"}
_FILE_PAYLOAD_NAMES = {"blobs", "libraries"}


def _skip_copy_name(name: str) -> bool:
    if name in _SKIP_RELOCATE or name in _SKIP_COPY_NAMES:
        return True
    return name.startswith("._")


def _programdata() -> Path:
    return Path(os.environ.get("PROGRAMDATA") or r"C:\ProgramData")


def _windows_prod_bootstrap() -> Path:
    return _programdata() / "mosaicWave"


def _windows_dev_bootstrap() -> Path:
    return _programdata() / "mosaicWave-dev"


def _installed_windows_payload() -> bool:
    """True when this package lives under Program Files\\mosaicWave (MSI)."""
    install_root = Path(__file__).resolve().parents[2]
    for key in ("ProgramFiles", "ProgramFiles(x86)"):
        raw = os.environ.get(key)
        if not raw:
            continue
        try:
            install_root.relative_to((Path(raw) / "mosaicWave").resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


def _windows_bootstrap_dir() -> Path:
    profile = (os.environ.get("MOSAICWAVE_PROFILE") or "").strip().lower()
    if profile in ("prod", "production", "msi"):
        return _windows_prod_bootstrap()
    if profile in ("dev", "debug", "development"):
        return _windows_dev_bootstrap()
    if _installed_windows_payload():
        return _windows_prod_bootstrap()
    return _windows_dev_bootstrap()


def _macos_prod_bootstrap() -> Path:
    return Path("/Library/Application Support/mosaicWave")


def _macos_dev_bootstrap() -> Path:
    """Same parent as packaged (ProgramData analogue), `-dev` suffix like Windows."""
    return Path("/Library/Application Support/mosaicWave-dev")


def _macos_legacy_user_bootstrap() -> Path:
    """Pre-split user library (no -dev). Packaged install may migrate from here."""
    return Path.home() / "Library" / "Application Support" / "mosaicWave"


def _macos_legacy_home_dev_bootstrap() -> Path:
    """Earlier debug path before it moved to /Library/Application Support/mosaicWave-dev."""
    return Path.home() / "Library" / "Application Support" / "mosaicWave-dev"


def _installed_macos_payload() -> bool:
    """True when this package lives under /Applications/mosaicWave.app (posix tarball)."""
    install_root = Path(__file__).resolve().parents[2]
    for base in ("/Applications/mosaicWave.app", "/Applications/mosaicWave"):
        try:
            install_root.relative_to(Path(base).resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


def _macos_bootstrap_dir() -> Path:
    profile = (os.environ.get("MOSAICWAVE_PROFILE") or "").strip().lower()
    if profile in ("prod", "production", "msi"):
        return _macos_prod_bootstrap()
    if profile in ("dev", "debug", "development"):
        return _macos_dev_bootstrap()
    if _installed_macos_payload():
        return _macos_prod_bootstrap()
    return _macos_dev_bootstrap()


def _platform_bootstrap_dir() -> Path:
    if os.name == "nt":
        return _windows_bootstrap_dir()
    if sys.platform == "darwin":
        return _macos_bootstrap_dir()
    profile = (os.environ.get("MOSAICWAVE_PROFILE") or "").strip().lower()
    xdg = os.environ.get("XDG_DATA_HOME")
    if profile in ("dev", "debug", "development"):
        if xdg:
            return Path(xdg) / "mosaicWave-dev"
        return Path.home() / ".local" / "share" / "mosaicWave-dev"
    if xdg:
        return Path(xdg) / "mosaicWave"
    return Path.home() / ".local" / "share" / "mosaicWave"


def _windows_legacy_localappdata() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "mosaicWave"


def data_dir_env_override() -> bool:
    return bool((os.environ.get("MOSAICWAVE_DATA_DIR") or "").strip())


def normalize_host_path_text(raw: str) -> str:
    """UNC and smb:// become a host filesystem path. Does not mount."""
    text = raw.strip()
    if not text:
        return ""
    from mosaicwave.storage.smbpath import parse_remote_share, native_share_path_text

    share = parse_remote_share(text)
    if share is not None:
        return str(native_share_path_text(share))
    if os.name != "nt" and text.startswith("\\\\"):
        return text.replace("\\", "/")
    return text


def _rebase_under(path: Path, old_root: Path, new_root: Path) -> Path:
    try:
        rel = path.resolve().relative_to(old_root.resolve())
    except ValueError:
        return path
    return (new_root / rel).resolve()


def _checkpoint_library_db(db: Path) -> None:
    """Flush WAL into library.db so a byte copy is a complete database."""
    if not db.is_file():
        return
    try:
        con = sqlite3.connect(os.fspath(db.resolve()), timeout=30.0)
        try:
            con.execute("PRAGMA busy_timeout=30000")
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            con.execute("PRAGMA journal_mode=DELETE")
            con.commit()
        finally:
            con.close()
    except sqlite3.Error:
        return


def _rewrite_source_root_uris(db: Path, old_root: Path, new_root: Path) -> None:
    """Rewrite source.root_uri on a local tempfile, then byte-copy over dest.

    Opening SQLite on the destination (SMB/AFP/iCloud) often fails with
    'unable to open database file'; the copy then rolled dest back to empty.
    """
    if not db.is_file() or db.stat().st_size == 0:
        return
    tmp_dir = Path(tempfile.mkdtemp(prefix="mosaicwave-db-"))
    tmp = tmp_dir / "library.db"
    try:
        shutil.copyfile(db, tmp)
        con = sqlite3.connect(os.fspath(tmp), timeout=30.0)
        try:
            con.execute("PRAGMA busy_timeout=30000")
            con.execute("PRAGMA journal_mode=DELETE")
            if "source" not in {
                row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }:
                return
            rows = list(con.execute("SELECT id, root_uri FROM source"))
            for source_id, uri in rows:
                if not uri:
                    continue
                updated = str(_rebase_under(Path(uri), old_root, new_root))
                if updated != uri:
                    con.execute("UPDATE source SET root_uri = ? WHERE id = ?", (updated, source_id))
            con.commit()
        except sqlite3.Error as exc:
            raise ValueError(
                "could not update library paths: "
                f"{exc}. If the new folder is on a NAS or iCloud, pick a local disk."
            ) from exc
        finally:
            con.close()
        _copy_path_bytes(tmp, db)
        for name in ("library.db-wal", "library.db-shm", "library.db-journal"):
            sidecar = db.parent / name
            sidecar.unlink(missing_ok=True)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _rm_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _copy_path_bytes(src: Path, dest: Path) -> None:
    """Copy content only. shutil.copy2/copystat chmod+xattr fails on many NAS mounts."""
    if src.is_symlink():
        try:
            dest.symlink_to(os.readlink(src))
            return
        except OSError:
            if not src.exists():
                raise
    if src.is_dir() and not src.is_symlink():
        dest.mkdir(parents=True, exist_ok=True)
        for child in src.iterdir():
            if _skip_copy_name(child.name):
                continue
            _copy_path_bytes(child, dest / child.name)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    with src.open("rb") as inf, dest.open("wb") as outf:
        shutil.copyfileobj(inf, outf, length=1024 * 1024)


def _delete_named(root: Path, names: list[str]) -> None:
    for name in names:
        path = root / name
        if path.exists() or path.is_symlink():
            _rm_path(path)


def clear_bootstrap_library_payload(bootstrap: Path, dest: Path) -> None:
    """Remove leftover photos/thumbs from the app folder after a NAS/local dest has them.

    library.db stays at bootstrap.
    """
    boot = bootstrap.expanduser().resolve()
    dest = dest.expanduser().resolve()
    if dest == boot:
        return
    _delete_named(boot, list(_FILE_PAYLOAD_NAMES))


def recover_library_db_to_bootstrap(bootstrap: Path, data_dir: Path) -> None:
    """Copy a leftover NAS library.db back to the app folder once (old Save folder layout)."""
    if data_dir_env_override():
        return
    boot = bootstrap.expanduser().resolve()
    dest = data_dir.expanduser().resolve()
    if dest == boot:
        return
    boot_db = boot / "library.db"
    dest_db = dest / "library.db"
    if _sqlite_has_library(boot_db):
        return
    if not dest_db.is_file() or dest_db.stat().st_size == 0:
        return
    boot.mkdir(parents=True, exist_ok=True)
    _checkpoint_library_db(dest_db)
    if boot_db.exists() or boot_db.is_symlink():
        _rm_path(boot_db)
    _copy_path_bytes(dest_db, boot_db)
    for name in ("library.db-wal", "library.db-shm", "library.db-journal"):
        extra = boot / name
        if extra.exists() or extra.is_symlink():
            _rm_path(extra)


def copy_data_dir(
    src: Path,
    dest: Path,
    *,
    replace_payload: bool = False,
    library_db: Path | None = None,
    move_sqlite: bool = False,
) -> list[str]:
    """Copy library files to dest. Leaves src in place. Rolls dest back on failure.

    Save folder keeps library.db at the app folder (rewrite URIs there). Legacy
    bootstrap relocate passes move_sqlite=True so the DB file moves with the folder.
    """
    src = src.resolve()
    dest = dest.resolve()
    if src == dest:
        return []
    if _is_under(dest, src) or _is_under(src, dest):
        raise ValueError("new folder cannot be inside the current data folder (or the reverse)")
    dest.mkdir(parents=True, exist_ok=True)
    if not src.is_dir():
        return []
    rewrite_target = library_db.resolve() if library_db is not None else src / "library.db"
    if move_sqlite:
        _checkpoint_library_db(src / "library.db")
    elif rewrite_target.is_file():
        _checkpoint_library_db(rewrite_target)
    if replace_payload:
        names = set(_FILE_PAYLOAD_NAMES)
        if move_sqlite:
            names.update(_LIBRARY_PAYLOAD_NAMES)
        for name in names:
            leftover = dest / name
            if leftover.exists() or leftover.is_symlink():
                _rm_path(leftover)
    items = [p for p in src.iterdir() if not _skip_copy_name(p.name)]
    if not move_sqlite:
        items = [p for p in items if p.name not in _SQLITE_FILE_NAMES]
    for item in items:
        target = dest / item.name
        if not target.exists():
            continue
        if item.is_dir() and target.is_dir() and next(target.iterdir(), None) is None:
            target.rmdir()
            continue
        if item.name in _LIBRARY_PAYLOAD_NAMES and (
            replace_payload or _empty_payload_item(target)
        ):
            _rm_path(target)
            if item.name == "library.db":
                for sidecar in ("library.db-wal", "library.db-shm", "library.db-journal"):
                    extra = dest / sidecar
                    if extra.exists() or extra.is_symlink():
                        _rm_path(extra)
            continue
        raise ValueError(f"destination already contains {item.name}")
    copied: list[str] = []
    rewritten = False
    try:
        for item in items:
            target = dest / item.name
            try:
                _copy_path_bytes(item, target)
            except OSError:
                if target.exists():
                    try:
                        _rm_path(target)
                    except OSError:
                        pass
                raise
            copied.append(item.name)
        if move_sqlite:
            _rewrite_source_root_uris(dest / "library.db", src, dest)
        elif rewrite_target.is_file():
            _rewrite_source_root_uris(rewrite_target, src, dest)
            rewritten = True
    except Exception as exc:
        if rewritten and rewrite_target.is_file():
            try:
                _rewrite_source_root_uris(rewrite_target, dest, src)
            except Exception:
                pass
        _delete_named(dest, copied)
        if isinstance(exc, OSError):
            raise ValueError(f"could not copy the data folder: {exc}") from exc
        raise
    return copied


def relocate_data_dir(src: Path, dest: Path) -> None:
    names = copy_data_dir(src, dest, move_sqlite=True)
    _delete_named(src.resolve(), names)


def _read_config_dict(bootstrap: Path) -> dict | None:
    path = bootstrap / _CONFIG_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def read_data_dir_config(bootstrap: Path) -> Path | None:
    data = _read_config_dict(bootstrap)
    if data is None:
        return None
    raw = data.get("data_dir")
    if not isinstance(raw, str) or not raw.strip():
        return None
    from mosaicwave.storage.smbpath import parse_remote_share

    if parse_remote_share(raw) is not None:
        return None
    return Path(raw).expanduser().resolve()


def read_data_dir_smb(bootstrap: Path) -> str | None:
    data = _read_config_dict(bootstrap)
    if data is None:
        return None
    raw = data.get("data_dir_smb")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    stored = data.get("data_dir")
    if isinstance(stored, str):
        from mosaicwave.storage.smbpath import parse_remote_share, smb_url_for_host_path

        if parse_remote_share(stored) is not None:
            return smb_url_for_host_path(stored)
    return None


def read_data_dir_mount(bootstrap: Path) -> str | None:
    data = _read_config_dict(bootstrap)
    if data is None:
        return None
    raw = data.get("data_dir_mount")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _smb_mount_wait() -> tuple[int, float]:
    try:
        attempts = int((os.environ.get("MOSAICWAVE_SMB_MOUNT_ATTEMPTS") or "15").strip())
    except ValueError:
        attempts = 15
    try:
        delay = float((os.environ.get("MOSAICWAVE_SMB_MOUNT_DELAY") or "2").strip())
    except ValueError:
        delay = 2.0
    return max(1, attempts), max(0.0, delay)


def mount_configured_share(url: str, *, mount_point: str | None = None) -> Path:
    """Mount smb:// (or wait for it) and return the host folder. Retries while the network comes up."""
    from mosaicwave.storage.smbpath import coerce_host_folder, refuse_unmounted_volumes_path

    attempts, delay = _smb_mount_wait()
    last: Exception | None = None
    for i in range(attempts):
        try:
            dest = coerce_host_folder(
                url,
                mount=True,
                wait_gui=2.0 if i == 0 else 0.0,
                mount_point=mount_point or "",
            )
            refuse_unmounted_volumes_path(dest)
            return dest
        except ValueError as exc:
            last = exc
            if i + 1 < attempts and delay:
                time.sleep(delay)
    raise ValueError(str(last) if last else f"could not mount {url}")


def resolve_configured_data_dir(
    raw: str, *, smb_url: str | None = None, mount_point: str | None = None
) -> Path:
    from mosaicwave.storage.smbpath import parse_remote_share, smb_url_for_host_path

    if parse_remote_share(raw) is not None:
        return mount_configured_share(smb_url or raw, mount_point=mount_point)
    url = smb_url or smb_url_for_host_path(raw)
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError("data_dir must be an absolute path")
    if url:
        return mount_configured_share(url, mount_point=mount_point)
    return path.resolve()


_LIBRARY_PAYLOAD_NAMES = {
    "library.db",
    "library.db-wal",
    "library.db-shm",
    "library.db-journal",
    "blobs",
    "libraries",
}


def _dir_has_files(path: Path) -> bool:
    if not path.is_dir():
        return False
    try:
        for child in path.rglob("*"):
            if child.is_file():
                return True
    except OSError:
        return True
    return False


def _sqlite_has_library(db: Path) -> bool:
    if not db.is_file() or db.stat().st_size == 0:
        return False
    try:
        con = sqlite3.connect(os.fspath(db), timeout=5.0)
        try:
            tables = {
                row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if not tables:
                return False
            for table in ("asset", "user"):
                if table not in tables:
                    continue
                count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                if count:
                    return True
            return False
        finally:
            con.close()
    except sqlite3.Error:
        return True


def data_dir_occupied(path: Path) -> bool:
    """True when path holds another library we must not overwrite.

    App files at bootstrap (session.key, runtime, config.json) do not count.
    A leftover library.db does not count (SQLite lives in the app folder).
    """
    return _dir_has_files(path / "libraries") or _dir_has_files(path / "blobs")


def _empty_payload_item(path: Path) -> bool:
    if not path.exists():
        return True
    if path.is_dir():
        try:
            return next(path.iterdir(), None) is None
        except OSError:
            return False
    if path.name == "library.db":
        return not _sqlite_has_library(path)
    if path.name.startswith("library.db"):
        return path.stat().st_size == 0
    return False


def ensure_data_dir_exists(path: Path) -> None:
    from mosaicwave.storage.smbpath import refuse_unmounted_volumes_path

    refuse_unmounted_volumes_path(path)
    path.mkdir(parents=True, exist_ok=True)


def ensure_writable_dir(path: Path) -> None:
    if path.exists() and not path.is_dir():
        raise ValueError("path exists and is not a folder")
    ensure_data_dir_exists(path)
    probe = path / "mosaicwave-write-test.tmp"
    try:
        probe.write_text("ok", encoding="utf-8")
    except OSError as e:
        raise ValueError(f"folder is not writable: {e}") from e
    finally:
        probe.unlink(missing_ok=True)


def write_data_dir_config(
    bootstrap: Path,
    data_dir: Path,
    *,
    smb_url: str | None = None,
    mount_point: str | None = None,
) -> None:
    from mosaicwave.storage.smbpath import smb_url_for_host_path

    bootstrap.mkdir(parents=True, exist_ok=True)
    dest = data_dir
    try:
        dest = data_dir.resolve()
    except OSError:
        dest = data_dir
    payload: dict[str, str] = {"data_dir": str(dest)}
    url = smb_url or smb_url_for_host_path(os.fspath(data_dir))
    if url:
        payload["data_dir_smb"] = url
        if mount_point and mount_point.strip():
            payload["data_dir_mount"] = mount_point.strip()
    tmp = bootstrap / "config.json.tmp"
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(bootstrap / _CONFIG_NAME)


_RESTART_FLAG = "restart.flag"


def restart_flag_path(bootstrap: Path) -> Path:
    return bootstrap / _RESTART_FLAG


def write_restart_flag(bootstrap: Path) -> None:
    bootstrap.mkdir(parents=True, exist_ok=True)
    restart_flag_path(bootstrap).write_text("1\n", encoding="utf-8")


def consume_restart_flag(bootstrap: Path | None = None) -> None:
    boot = bootstrap or _platform_bootstrap_dir()
    restart_flag_path(boot).unlink(missing_ok=True)


def schedule_api_restart(bootstrap: Path) -> bool:
    """Exit this API process after Save folder so the next start remounts from config.

    Skipped under pytest. Debug `start.sh` respawns when restart.flag is present.
    Packaged LaunchDaemon / service KeepAlive brings the process back.
    """
    if "pytest" in sys.modules:
        return False
    import threading

    write_restart_flag(bootstrap)

    def _die() -> None:
        os._exit(0)

    threading.Timer(1.0, _die).start()
    return True


def resolve_data_dir(*, bootstrap: Path | None = None) -> Path:
    boot = (bootstrap or _platform_bootstrap_dir()).resolve()
    raw = (os.environ.get("MOSAICWAVE_DATA_DIR") or "").strip()
    if raw:
        return resolve_configured_data_dir(raw)
    data = _read_config_dict(boot)
    if data is not None:
        stored = data.get("data_dir")
        smb = data.get("data_dir_smb")
        smb_url = smb.strip() if isinstance(smb, str) and smb.strip() else None
        mount = data.get("data_dir_mount")
        mount_point = mount.strip() if isinstance(mount, str) and mount.strip() else None
        from mosaicwave.storage.smbpath import parse_remote_share

        text = stored.strip() if isinstance(stored, str) and stored.strip() else ""
        if smb_url or parse_remote_share(text) is not None:
            return resolve_configured_data_dir(
                text or (smb_url or ""), smb_url=smb_url, mount_point=mount_point
            )
        if text:
            return resolve_configured_data_dir(text)
    configured = read_data_dir_config(boot)
    if configured is not None:
        return configured
    if os.name == "nt":
        if (boot / "library.db").is_file():
            return boot
        legacy = _windows_legacy_localappdata()
        if (legacy / "library.db").is_file():
            relocate_data_dir(legacy, boot)
        return boot
    if sys.platform == "darwin":
        if (boot / "library.db").is_file():
            return boot
        if boot.resolve() == _macos_prod_bootstrap().resolve():
            legacy = _macos_legacy_user_bootstrap()
            if (legacy / "library.db").is_file():
                relocate_data_dir(legacy, boot)
            return boot
        for legacy in (_macos_legacy_home_dev_bootstrap(), _macos_legacy_user_bootstrap()):
            if legacy.resolve() != boot.resolve() and (legacy / "library.db").is_file():
                relocate_data_dir(legacy, boot)
                break
        return boot
    profile = (os.environ.get("MOSAICWAVE_PROFILE") or "").strip().lower()
    if profile in ("dev", "debug", "development"):
        if (boot / "library.db").is_file():
            return boot
        xdg = os.environ.get("XDG_DATA_HOME")
        legacy = Path(xdg) / "mosaicWave" if xdg else Path.home() / ".local" / "share" / "mosaicWave"
        if legacy.resolve() != boot.resolve() and (legacy / "library.db").is_file():
            relocate_data_dir(legacy, boot)
        return boot
    return boot


def _default_data_dir() -> Path:
    return resolve_data_dir()


def _normalize_platform(raw: str | None) -> str:
    value = (raw or "standalone").strip().lower()
    if value not in ("standalone", "qnap"):
        return "standalone"
    return value


def session_secret_for(
    bootstrap: Path, secret: str = "", *, data_dir: Path | None = None
) -> str:
    """HMAC key lives at bootstrap (next to config.json), not in a moved library folder."""
    if secret.strip():
        return secret.strip()
    bootstrap.mkdir(parents=True, exist_ok=True)
    key_path = bootstrap / "session.key"
    if not key_path.is_file() and data_dir is not None:
        legacy = data_dir / "session.key"
        try:
            if legacy.is_file() and legacy.resolve() != key_path.resolve():
                key_path.write_bytes(legacy.read_bytes())
        except OSError:
            pass
    if key_path.is_file():
        stored = key_path.read_text(encoding="utf-8").strip()
        if stored:
            return stored
    generated = secrets.token_hex(32)
    key_path.write_text(generated, encoding="utf-8")
    return generated


def _bundled_web_root() -> Path | None:
    """Next.js export next to the install prefix (…/server/mosaicwave/config.py → …/web)."""
    try:
        root = Path(__file__).resolve().parents[2] / "web"
    except (IndexError, OSError):
        return None
    if (root / "index.html").is_file():
        return root.resolve()
    return None


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    takeout_dir: Path | None
    source_dir: Path | None
    platform: str = "standalone"
    session_secret: str = ""
    web_root: Path | None = None
    bind_host: str = "127.0.0.1"
    bind_port: int = 8000
    bootstrap_dir: Path | None = None

    def __post_init__(self) -> None:
        if self.bootstrap_dir is None:
            object.__setattr__(self, "bootstrap_dir", Path(self.data_dir))

    @property
    def database_path(self) -> Path:
        if data_dir_env_override():
            return self.data_dir / "library.db"
        boot = self.bootstrap_dir if self.bootstrap_dir is not None else self.data_dir
        return Path(boot) / "library.db"

    @property
    def blob_dir(self) -> Path:
        return self.data_dir / "blobs"

    @classmethod
    def from_env(cls) -> Settings:
        boot = _platform_bootstrap_dir()
        data_dir = resolve_data_dir(bootstrap=boot)
        takeout = os.environ.get("MOSAICWAVE_TAKEOUT_DIR")
        source = os.environ.get("MOSAICWAVE_SOURCE_DIR")
        web = (os.environ.get("MOSAICWAVE_WEB_ROOT") or "").strip()
        web_root = Path(web).expanduser() if web else _bundled_web_root()
        host = (os.environ.get("MOSAICWAVE_HOST") or "127.0.0.1").strip() or "127.0.0.1"
        port_raw = (os.environ.get("MOSAICWAVE_PORT") or "8000").strip()
        try:
            port = int(port_raw)
        except ValueError:
            port = 8000
        recover_library_db_to_bootstrap(boot, data_dir)
        return cls(
            data_dir=data_dir,
            takeout_dir=Path(takeout).expanduser() if takeout else None,
            source_dir=Path(source).expanduser() if source else None,
            platform=_normalize_platform(os.environ.get("MOSAICWAVE_PLATFORM")),
            session_secret=(os.environ.get("MOSAICWAVE_SECRET") or "").strip(),
            web_root=web_root,
            bind_host=host,
            bind_port=port,
            bootstrap_dir=boot,
        )


def with_data_dir(settings: Settings, data_dir: Path) -> Settings:
    return replace(settings, data_dir=data_dir.resolve())
