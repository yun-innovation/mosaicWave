"""Export photos as ordinary files (doc/backup.md, "Export").

An export is a folder under ``exports/{user_id}/`` holding zip parts and, for files too big for a part,
the files themselves. It is the user's own originals only, with no database and no thumbnails, so it
unzips into plain files; it is not a backup and nothing restores from it.

Two layouts:

* ``plain``: ``<album>/<file>`` and ``Photos/<file>`` for photos that are in no album.
* ``takeout``: Google Takeout's tree, ``Takeout/Google Photos/<album>/<file>`` with a ``<file>.json``
  sidecar (title, ``photoTakenTime``, location) next to each file, and ``Photos from YYYY`` for
  photos in no album. mosaicWave's own Takeout import reads it back.

Splitting follows Google's: parts fill up to the chosen size and a file that is bigger than that is not
zipped. In the Takeout layout it sits beside the zips as ``<name>-NNN.<ext>``, where NNN is the zip number
it would have taken (its sidecar stays inside a zip); in the plain layout it sits beside the zips under
the path it would have had in the zip, so unzipping every part into one folder gives the whole tree.

The export is built in a ``.export-<stamp>.part`` folder and renamed when complete, so a cancel or a
crash never leaves something that looks finished.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from mosaicwave import __version__
from mosaicwave.archive import ArchiveError
from mosaicwave.config import Settings
from mosaicwave.library.takeout import filestore_for_source
from mosaicwave.models import Album, AlbumAsset, Asset, Source
from mosaicwave.restore import _require_space
from mosaicwave.storage.errors import InvalidPath, NotFound, PermissionDenied

FORMAT = 1
KIND_EXPORT = "export"
LAYOUT_PLAIN = "plain"
LAYOUT_TAKEOUT = "takeout"
LAYOUTS = (LAYOUT_PLAIN, LAYOUT_TAKEOUT)
INFO_NAME = "mosaicwave-export.json"
EXPORT_NAME = re.compile(r"^export-\d{8}T\d{6}Z$")
PART_PREFIX = ".export-"
PART_SUFFIX = ".part"
TAKEOUT_ROOT = "Takeout/Google Photos"
CHUNK = 1024 * 1024
SIDECAR_ALLOWANCE = 2048
IN_LIMIT = 500
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

Progress = Callable[[int, int, str], None]


class ExportError(ArchiveError):
    """The export cannot be made (nothing to export, nothing readable)."""


@dataclass
class Entry:
    asset_id: str
    folder: str
    name: str
    source_id: str
    rel: str
    size: int
    when: datetime
    extra: dict
    in_album: bool


@dataclass
class Plan:
    entries: list[Entry]
    albums: int
    warnings: list[str] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(e.size for e in self.entries)


@dataclass(frozen=True)
class ExportResult:
    folder: Path
    info: dict


# --- names ---------------------------------------------------------------------------------------


def export_user_dir(settings: Settings, user_id: str) -> Path:
    return settings.export_dir / user_id


def _stamp(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")


def _clean(name: str, fallback: str) -> str:
    """One path component that any file system accepts."""
    text = _BAD_CHARS.sub("_", name).strip().rstrip(". ")
    text = text[:120].rstrip(". ")
    return text or fallback


def _unique(name: str, taken: set[str], layout: str) -> str:
    """``name`` if free, else ``name (2)`` (plain) or ``name(1)`` (Takeout's own style)."""
    if name.lower() not in taken:
        return name
    path = PurePosixPath(name)
    stem, suffix = path.stem, path.suffix
    n = 2 if layout == LAYOUT_PLAIN else 1
    while True:
        candidate = f"{stem} ({n}){suffix}" if layout == LAYOUT_PLAIN else f"{stem}({n}){suffix}"
        if candidate.lower() not in taken:
            return candidate
        n += 1


def _utc(value: datetime | None, fallback: float) -> datetime:
    if value is None:
        try:
            return datetime.fromtimestamp(fallback or 0, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return datetime.fromtimestamp(0, tz=timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _chunks(items: list[str]) -> Iterator[list[str]]:
    for start in range(0, len(items), IN_LIMIT):
        yield items[start : start + IN_LIMIT]


def _extra(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


# --- plan ----------------------------------------------------------------------------------------


def missing_albums(lib: Session, album_ids: list[str]) -> list[str]:
    found: set[str] = set()
    for chunk in _chunks(album_ids):
        found.update(
            lib.scalars(select(Album.id).where(Album.id.in_(chunk), Album.deleted_at.is_(None)))
        )
    return [a for a in album_ids if a not in found]


def plan_export(
    lib: Session,
    layout: str,
    *,
    album_ids: list[str] | None = None,
    asset_ids: list[str] | None = None,
    everything: bool = False,
) -> Plan:
    """Work out which file goes where. A photo in albums is exported into each of them; a photo in no
    album (or picked on its own) goes into the ``Photos`` folder once. Nothing is read or written."""
    album_ids = list(dict.fromkeys(album_ids or []))
    asset_ids = list(dict.fromkeys(asset_ids or []))
    warnings: list[str] = []
    sources = {s.id: s for s in lib.scalars(select(Source).where(Source.deleted_at.is_(None)))}

    album_stmt = select(Album).where(Album.deleted_at.is_(None)).order_by(Album.name, Album.id)
    albums: list[Album] = []
    if everything:
        albums = list(lib.scalars(album_stmt))
    else:
        for chunk in _chunks(album_ids):
            albums.extend(lib.scalars(album_stmt.where(Album.id.in_(chunk))))
        albums.sort(key=lambda a: (a.name, a.id))

    member_stmt = (
        select(AlbumAsset.album_id, Asset)
        .join(Asset, Asset.id == AlbumAsset.asset_id)
        .where(AlbumAsset.deleted_at.is_(None), Asset.deleted_at.is_(None))
    )
    members: dict[str, list[Asset]] = {a.id: [] for a in albums}
    if everything:
        for album_id, asset in lib.execute(member_stmt):
            if album_id in members:
                members[album_id].append(asset)
    else:
        for chunk in _chunks([a.id for a in albums]):
            for album_id, asset in lib.execute(member_stmt.where(AlbumAsset.album_id.in_(chunk))):
                members[album_id].append(asset)

    covered = {asset.id for rows in members.values() for asset in rows}
    loose: list[Asset] = []
    if everything:
        loose = [
            a
            for a in lib.scalars(select(Asset).where(Asset.deleted_at.is_(None)).order_by(Asset.id))
            if a.id not in covered
        ]
    else:
        found: set[str] = set()
        for chunk in _chunks(asset_ids):
            for asset in lib.scalars(select(Asset).where(Asset.id.in_(chunk), Asset.deleted_at.is_(None))):
                found.add(asset.id)
                if asset.id not in covered:
                    loose.append(asset)
        if len(found) < len(asset_ids):
            warnings.append(f"{len(asset_ids) - len(found)} photo(s) were not found and are not in the export")

    prefix = f"{TAKEOUT_ROOT}/" if layout == LAYOUT_TAKEOUT else ""
    folders: dict[str, str] = {}  # lower-case folder -> owner key, so two names never share a folder

    def folder_for(key: str, wanted: str) -> str:
        name = wanted
        n = 1
        while folders.get(name.lower(), key) != key:
            n += 1
            name = f"{wanted} ({n})" if layout == LAYOUT_PLAIN else f"{wanted}({n - 1})"
        folders[name.lower()] = key
        return prefix + name

    names: dict[str, set[str]] = {}
    entries: list[Entry] = []
    skipped = 0

    def add(asset: Asset, folder: str, in_album: bool) -> None:
        nonlocal skipped
        source = sources.get(asset.source_id)
        if source is None:
            skipped += 1
            return
        taken = names.setdefault(folder, set())
        base = _clean(PurePosixPath(asset.relative_path).name, "file")
        name = _unique(base, taken, layout)
        taken.add(name.lower())
        entries.append(
            Entry(
                asset_id=asset.id,
                folder=folder,
                name=name,
                source_id=asset.source_id,
                rel=asset.relative_path,
                size=int(asset.size or 0),
                when=_utc(asset.taken_at, asset.mtime),
                extra=_extra(asset.extra_json),
                in_album=in_album,
            )
        )

    for album in albums:
        folder = folder_for(f"album:{album.id}", _clean(album.name, "Album"))
        for asset in sorted(members[album.id], key=lambda a: (a.relative_path.lower(), a.id)):
            add(asset, folder, True)
    for asset in sorted(loose, key=lambda a: (a.relative_path.lower(), a.id)):
        when = _utc(asset.taken_at, asset.mtime)
        bucket = "Photos" if layout == LAYOUT_PLAIN else f"Photos from {when.year}"
        add(asset, folder_for(f"bucket:{bucket}", bucket), False)
    if skipped:
        warnings.append(f"{skipped} photo(s) belong to a removed folder and are not in the export")
    entries.sort(key=lambda e: (e.folder.lower(), e.name.lower(), e.asset_id))
    return Plan(entries=entries, albums=len(albums), warnings=warnings)


# --- write ---------------------------------------------------------------------------------------


def _formatted(when: datetime) -> str:
    hour = when.hour % 12 or 12
    return (
        f"{when.strftime('%b')} {when.day}, {when.year}, "
        f"{hour}:{when.minute:02d}:{when.second:02d} {'AM' if when.hour < 12 else 'PM'} UTC"
    )


def sidecar_json(entry: Entry) -> bytes:
    """A Takeout-style ``<file>.json``: what mosaicWave's own import (and other Takeout readers) use."""
    stamp = {"timestamp": str(int(entry.when.timestamp())), "formatted": _formatted(entry.when)}
    data: dict = {
        "title": entry.name,
        "description": str(entry.extra.get("caption") or ""),
        "creationTime": stamp,
        "photoTakenTime": stamp,
    }
    geo = entry.extra.get("geo")
    if isinstance(geo, dict) and geo.get("lat") is not None and geo.get("lng") is not None:
        data["geoData"] = {
            "latitude": geo.get("lat"),
            "longitude": geo.get("lng"),
            "altitude": geo.get("alt") or 0.0,
            "latitudeSpan": 0.0,
            "longitudeSpan": 0.0,
        }
    return json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")


def _zip_time(when: datetime) -> tuple[int, int, int, int, int, int]:
    if when.year < 1980:
        return (1980, 1, 1, 0, 0, 0)
    if when.year > 2107:
        return (2107, 12, 31, 23, 59, 58)
    return (when.year, when.month, when.day, when.hour, when.minute, when.second - when.second % 2)


class _Parts:
    """The zip parts of one export, numbered the way Google numbers them."""

    def __init__(self, dest: Path, stamp: str, layout: str, limit: int | None) -> None:
        self.dest = dest
        self.stamp = stamp
        self.layout = layout
        self.limit = limit
        self.number = 0
        self.zf: zipfile.ZipFile | None = None
        self.used = 0
        self.zips = 0

    def _open(self) -> zipfile.ZipFile:
        self.number += 1
        prefix = "takeout" if self.layout == LAYOUT_TAKEOUT else "mosaicwave"
        path = self.dest / f"{prefix}-{self.stamp}-{self.number:03d}.zip"
        self.zf = zipfile.ZipFile(path, "w", allowZip64=True, strict_timestamps=False)
        self.used = 0
        self.zips += 1
        return self.zf

    def close(self) -> None:
        if self.zf is not None:
            self.zf.close()
            self.zf = None

    def current(self) -> zipfile.ZipFile:
        return self.zf if self.zf is not None else self._open()

    def for_file(self, size: int) -> zipfile.ZipFile:
        """The part this file goes into: a fresh one when it would push the open part past the limit."""
        if self.zf is not None and self.limit and self.used > 0 and self.used + size > self.limit:
            self.close()
        return self.current()

    def reserve(self) -> int:
        """A part number for a file that is not zipped (its name carries the number)."""
        self.number += 1
        return self.number


def _copy(src, dst, check: Callable[[], None]) -> int:  # type: ignore[no-untyped-def]
    total = 0
    while True:
        check()
        chunk = src.read(CHUNK)
        if not chunk:
            return total
        dst.write(chunk)
        total += len(chunk)


def export_library(
    settings: Settings,
    lib: Session,
    user_id: str,
    *,
    layout: str,
    split_bytes: int | None = None,
    album_ids: list[str] | None = None,
    asset_ids: list[str] | None = None,
    everything: bool = False,
    on_progress: Progress | None = None,
    check: Callable[[], None] | None = None,
) -> ExportResult:
    if layout not in LAYOUTS:
        raise ExportError(f"unknown layout: {layout!r}")
    check = check or (lambda: None)
    plan = plan_export(lib, layout, album_ids=album_ids, asset_ids=asset_ids, everything=everything)
    if not plan.entries:
        raise ExportError("there is nothing to export")
    limit = split_bytes if split_bytes and split_bytes > 0 else None

    out_dir = export_user_dir(settings, user_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    _require_space(out_dir, plan.total_bytes + SIDECAR_ALLOWANCE * len(plan.entries))
    now = datetime.now(timezone.utc)
    while True:
        stamp = _stamp(now)
        final = out_dir / f"export-{stamp}"
        if not final.exists():
            break
        now = datetime.fromtimestamp(now.timestamp() + 1, tz=timezone.utc)
    work = out_dir / f"{PART_PREFIX}{stamp}{PART_SUFFIX}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()

    stores: dict[str, object] = {}
    sources = {s.id: s for s in lib.scalars(select(Source).where(Source.deleted_at.is_(None)))}
    parts = _Parts(work, stamp, layout, limit)
    warnings = list(plan.warnings)
    written = 0
    loose_count = 0
    total_bytes = 0
    vanished = 0
    try:
        total = len(plan.entries)
        for index, entry in enumerate(plan.entries, start=1):
            check()
            store = stores.get(entry.source_id)
            if store is None:
                store = stores[entry.source_id] = filestore_for_source(sources[entry.source_id])
            try:
                size = store.stat(entry.rel).size  # type: ignore[attr-defined]
                reader = store.open_read(entry.rel)  # type: ignore[attr-defined]
                src = reader.__enter__()
            except (NotFound, InvalidPath, PermissionDenied, OSError):
                vanished += 1
                if on_progress is not None:
                    on_progress(index, total, "")
                continue
            arcname = f"{entry.folder}/{entry.name}"
            try:
                if limit and size > limit:
                    if layout == LAYOUT_TAKEOUT:
                        number = parts.reserve()
                        stem = PurePosixPath(entry.name)
                        target = work / f"{stem.stem}-{number:03d}{stem.suffix}"
                    else:
                        target = work.joinpath(*arcname.split("/"))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        with target.open("wb") as dst:
                            total_bytes += _copy(src, dst, check)
                    except BaseException:
                        target.unlink(missing_ok=True)
                        raise
                    stamp_time = entry.when.timestamp()
                    os.utime(target, (stamp_time, stamp_time))
                    loose_count += 1
                else:
                    zf = parts.for_file(size)
                    info = zipfile.ZipInfo(arcname, _zip_time(entry.when))
                    info.compress_type = zipfile.ZIP_STORED
                    info.external_attr = 0o644 << 16
                    info.file_size = size
                    with zf.open(info, "w", force_zip64=size >= 2**30) as dst:
                        count = _copy(src, dst, check)
                    parts.used += count
                    total_bytes += count
                written += 1
            finally:
                reader.__exit__(None, None, None)
            if layout == LAYOUT_TAKEOUT:
                body = sidecar_json(entry)
                info = zipfile.ZipInfo(f"{arcname}.json", _zip_time(entry.when))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                parts.current().writestr(info, body)
                parts.used += len(body)
            if on_progress is not None:
                on_progress(index, total, "")
        parts.close()
        if vanished:
            warnings.append(f"{vanished} file(s) could not be read and are not in the export")
        if written == 0:
            raise ExportError("none of the files could be read")
        info_data = {
            "format": FORMAT,
            "kind": KIND_EXPORT,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "app_version": __version__,
            "layout": layout,
            "split_bytes": limit,
            "assets": written,
            "albums": plan.albums,
            "zips": parts.zips,
            "unzipped": loose_count,
            "total_bytes": total_bytes,
            "warnings": warnings,
        }
        (work / INFO_NAME).write_text(json.dumps(info_data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(work, final)
        return ExportResult(final, info_data)
    except BaseException:
        parts.close()
        shutil.rmtree(work, ignore_errors=True)
        raise


# --- list, download, delete ----------------------------------------------------------------------


def export_folder(settings: Settings, user_id: str, name: str) -> Path | None:
    if not EXPORT_NAME.match(name):
        return None
    folder = export_user_dir(settings, user_id) / name
    return folder if folder.is_dir() else None


def export_files(folder: Path) -> list[dict]:
    """Every file of an export, relative to its folder: the zip parts and the files not zipped."""
    files: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
        dirnames.sort()
        for filename in sorted(filenames):
            path = Path(dirpath) / filename
            rel = path.relative_to(folder).as_posix()
            if rel == INFO_NAME or path.is_symlink():
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            files.append({"path": rel, "size": size, "zipped": "/" not in rel and rel.lower().endswith(".zip")})
    return files


def describe_export(folder: Path) -> dict | None:
    try:
        info = json.loads((folder / INFO_NAME).read_text(encoding="utf-8"))
        if info.get("kind") != KIND_EXPORT:
            return None
    except (OSError, ValueError):
        return None
    files = export_files(folder)
    return {
        "name": folder.name,
        "created_at": str(info.get("created_at", "")),
        "layout": str(info.get("layout", LAYOUT_PLAIN)),
        "split_bytes": info.get("split_bytes"),
        "assets": int(info.get("assets", 0)),
        "albums": int(info.get("albums", 0)),
        "total_bytes": int(info.get("total_bytes", 0)),
        "size": sum(f["size"] for f in files),
        "warnings": [str(w) for w in info.get("warnings", [])],
        "files": files,
    }


def list_exports(settings: Settings, user_id: str) -> list[dict]:
    base = export_user_dir(settings, user_id)
    if not base.is_dir():
        return []
    found = [
        d
        for d in sorted(base.iterdir(), key=lambda p: p.name, reverse=True)
        if EXPORT_NAME.match(d.name) and d.is_dir()
    ]
    return [view for d in found if (view := describe_export(d)) is not None]


def export_file_path(folder: Path, rel: str) -> Path | None:
    """One file inside an export, or None. Refuses anything that could leave the folder."""
    parts = rel.split("/")
    if not rel or any(p in ("", ".", "..") or "\\" in p or ":" in p for p in parts) or rel == INFO_NAME:
        return None
    path = folder.joinpath(*parts)
    try:
        path.resolve().relative_to(folder.resolve())
    except (ValueError, OSError):
        return None
    return path if path.is_file() and not path.is_symlink() else None


def delete_export(folder: Path) -> None:
    shutil.rmtree(folder)


def remove_stale_exports(settings: Settings) -> None:
    """Delete half-built exports a crash left behind (nothing else lives in ``.export-*.part``)."""
    if not settings.export_dir.is_dir():
        return
    for user_dir in settings.export_dir.iterdir():
        if not user_dir.is_dir():
            continue
        for entry in user_dir.glob(f"{PART_PREFIX}*{PART_SUFFIX}"):
            shutil.rmtree(entry, ignore_errors=True)


__all__ = [
    "ExportError",
    "ExportResult",
    "KIND_EXPORT",
    "LAYOUTS",
    "LAYOUT_PLAIN",
    "LAYOUT_TAKEOUT",
    "delete_export",
    "describe_export",
    "export_file_path",
    "export_folder",
    "export_library",
    "export_user_dir",
    "list_exports",
    "missing_albums",
    "plan_export",
    "remove_stale_exports",
]
