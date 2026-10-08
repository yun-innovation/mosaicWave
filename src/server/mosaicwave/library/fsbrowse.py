"""List host directories for the admin Takeout/source picker (Phase 4: admin-only)."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from mosaicwave.config import normalize_host_path_text
from mosaicwave.library.media import TAKEOUT_ZIP
from mosaicwave.storage.smbpath import _app_volume_root, coerce_host_folder, parse_remote_share

_DRIVE = re.compile(r"^[A-Za-z]:$")
_WIN_PATH = re.compile(r"^[A-Za-z]:[\\/]")


def posix_default_root() -> Path:
    """QNAP shares live under /share; Linux standalone falls back to $HOME."""
    share = Path("/share")
    try:
        if share.is_dir():
            return share
    except OSError:
        pass
    return Path.home()


def _volume_root() -> Path | None:
    if sys.platform != "darwin":
        return None
    path = Path("/Volumes")
    try:
        return path if path.is_dir() else None
    except OSError:
        return None


def _under_volumes(path: Path) -> bool:
    posix = path.as_posix()
    return posix == "/Volumes" or posix.startswith("/Volumes/")


def _listdir_rows(resolved: Path) -> list[tuple[str, bool]]:
    names = sorted(os.listdir(os.fspath(resolved)), key=str.lower)
    rows: list[tuple[str, bool]] = []
    for name in names:
        item = resolved / name
        try:
            is_dir = item.is_dir()
        except OSError:
            is_dir = True
        rows.append((name, is_dir))
    return rows


def _entries_from_rows(resolved: Path, rows: list[tuple[str, bool]]) -> tuple[list[FsEntry], int]:
    entries: list[FsEntry] = []
    zip_count = 0
    for name, is_dir in rows:
        item = resolved / name
        if is_dir:
            entries.append(FsEntry(name=name, path=str(item), is_dir=True))
            continue
        if TAKEOUT_ZIP.match(name):
            zip_count += 1
            entries.append(FsEntry(name=name, path=str(item), is_dir=False))
    return entries, zip_count


def _unreadable_volume_message(resolved: Path) -> str:
    if _app_volume_root(resolved.as_posix()):
        return (
            f"cannot list {resolved} (permission). This is a previous mosaicWave dest "
            "mounted as a system service. This process cannot list it. Remount with Save folder."
        )
    if _under_volumes(resolved):
        return (
            f"cannot list {resolved} (permission). mosaicWave lists as a system service "
            "and does not use Finder or Mac-user mounts. Use SMB host/share in Settings, "
            "or leave mount point blank."
        )
    return f"cannot list {resolved} (permission)."


def _scan_resolved(resolved: Path) -> tuple[list[FsEntry], int, str | None]:
    try:
        rows = _listdir_rows(resolved)
    except OSError:
        return [], 0, _unreadable_volume_message(resolved)
    return (*_entries_from_rows(resolved, rows), None)


def _posix_place_roots() -> list[FsEntry]:
    entries = [FsEntry(name="Home", path=str(Path.home()), is_dir=True)]
    volumes = _volume_root()
    if volumes is None:
        return entries
    entries.append(FsEntry(name="Volumes", path=str(volumes), is_dir=True))
    child_entries, _, _ = _scan_resolved(volumes)
    for child in child_entries:
        if child.is_dir:
            entries.append(FsEntry(name=child.name, path=child.path, is_dir=True))
    return entries


def _looks_windows_path(text: str) -> bool:
    stripped = text.strip()
    return bool(_WIN_PATH.match(stripped)) or stripped.startswith("\\\\")


@dataclass
class FsEntry:
    name: str
    path: str
    is_dir: bool


@dataclass
class FsListing:
    path: str
    parent: str | None
    entries: list[FsEntry]
    takeout_zip_count: int
    warning: str | None = None


def _windows_drives() -> list[FsEntry]:
    drives: list[FsEntry] = []
    for code in range(ord("A"), ord("Z") + 1):
        letter = chr(code)
        root = Path(f"{letter}:\\")
        try:
            if root.exists():
                drives.append(FsEntry(name=f"{letter}:", path=str(root), is_dir=True))
        except OSError:
            continue
    return drives


def _normalize_path(raw: str | None) -> Path | None:
    if raw is None or not raw.strip():
        return None
    text = raw.strip()
    if _DRIVE.match(text):
        text = text + "\\"
    path = Path(text).expanduser()
    return path


def list_host_dir(raw: str | None) -> FsListing:
    if raw:
        if parse_remote_share(raw) is not None:
            try:
                raw = str(coerce_host_folder(raw, mount=True))
            except ValueError as exc:
                raise FileNotFoundError(str(exc)) from exc
        elif os.name != "nt" and _looks_windows_path(raw):
            converted = normalize_host_path_text(raw)
            raw = converted if converted.startswith("//") else None
    path = _normalize_path(raw)
    if path is None:
        if os.name == "nt":
            return FsListing(path="", parent=None, entries=_windows_drives(), takeout_zip_count=0)
        if sys.platform == "darwin":
            return FsListing(path="", parent=None, entries=_posix_place_roots(), takeout_zip_count=0)
        return list_host_dir(str(posix_default_root()))

    try:
        resolved = path.resolve()
    except OSError as exc:
        raise FileNotFoundError(str(exc)) from exc
    try:
        is_dir = resolved.is_dir()
    except OSError as exc:
        raise FileNotFoundError(str(exc)) from exc
    if not is_dir:
        raise FileNotFoundError(str(resolved))

    entries, zip_count, warning = _scan_resolved(resolved)

    parent: str | None
    if os.name == "nt" and resolved.parent == resolved:
        parent = ""
    elif resolved.parent == resolved:
        parent = None
    else:
        parent = str(resolved.parent)

    return FsListing(
        path=str(resolved),
        parent=parent,
        entries=entries,
        takeout_zip_count=zip_count,
        warning=warning,
    )
