"""FileStore over a Google Takeout download: zip parts + loose oversized files, no unzip."""

from __future__ import annotations

import calendar
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from mosaicwave.library.media import (
    TAKEOUT_ZIP,
    is_media,
    is_photos_root_name,
    parse_oversize_name,
    sidecar_media_name,
)
from mosaicwave.storage.errors import InvalidPath, NotFound, NotSupported
from mosaicwave.storage.filestore import FileStat
from mosaicwave.storage.paths import normalize_rel


@dataclass(frozen=True)
class _Loc:
    zip_path: Path | None
    inner: str | None
    file_path: Path | None
    size: int
    mtime: float


def has_takeout_zips(root: Path) -> bool:
    if not root.is_dir():
        return False
    return any(TAKEOUT_ZIP.match(p.name) for p in root.iterdir() if p.is_file())


def takeout_zip_paths(root: Path) -> list[Path]:
    return sorted(
        p for p in root.iterdir() if p.is_file() and TAKEOUT_ZIP.match(p.name)
    )


def zip_dump_has_media(root: Path) -> tuple[bool, list[str]]:
    """Open zip directories until one media file is found. Does not index the whole dump."""
    errors: list[str] = []
    for zpath in takeout_zip_paths(root):
        try:
            with _open_zip(zpath) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    name = PurePosixPath(info.filename).name
                    if is_media(name):
                        return True, errors
        except (OSError, zipfile.BadZipFile) as exc:
            errors.append(f"{zpath.name}: {exc}")
    return False, errors


def _posix(name: str) -> list[str]:
    return [p for p in name.replace("\\", "/").strip("/").split("/") if p]


def _safe_parts(parts: list[str]) -> bool:
    return bool(parts) and not any(p in (".", "..") for p in parts)


def _zip_mtime(info: zipfile.ZipInfo) -> float:
    try:
        t = info.date_time
        dt = datetime(t[0], t[1], t[2], t[3], t[4], t[5], tzinfo=timezone.utc)
        return calendar.timegm(dt.timetuple())
    except (ValueError, OverflowError, OSError):
        return 0.0


def _open_zip(path: Path) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path, metadata_encoding="utf-8")
    except TypeError:
        return zipfile.ZipFile(path)


def _detect_prefix(inners: list[list[str]]) -> tuple[str, ...]:
    for parts in inners:
        if (
            len(parts) >= 2
            and parts[0].lower() == "takeout"
            and is_photos_root_name(parts[1])
        ):
            return (parts[0], parts[1])
        if parts and is_photos_root_name(parts[0]):
            return (parts[0],)
    return ("Takeout",)


def _strip_prefix(parts: list[str], prefix: tuple[str, ...]) -> list[str] | None:
    if len(parts) < len(prefix):
        return None
    if [p.lower() for p in parts[: len(prefix)]] != [p.lower() for p in prefix]:
        return None
    return parts[len(prefix) :]


class TakeoutZipStore:
    """Virtual photos tree from takeout-*.zip plus dump-root oversized media."""

    def __init__(self, dump: str | Path) -> None:
        self._dump = Path(dump).expanduser().resolve()
        self.zip_errors: list[str] = []
        self.photos_root_name: str | None = None
        self._files: dict[str, _Loc] = {}
        self._build()

    @property
    def dump(self) -> Path:
        return self._dump

    def _build(self) -> None:
        raw: list[tuple[list[str], _Loc]] = []
        for zpath in takeout_zip_paths(self._dump):
            try:
                with _open_zip(zpath) as zf:
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        parts = _posix(info.filename)
                        if not _safe_parts(parts):
                            continue
                        loc = _Loc(
                            zip_path=zpath,
                            inner=info.filename,
                            file_path=None,
                            size=info.file_size,
                            mtime=_zip_mtime(info),
                        )
                        raw.append((parts, loc))
            except (OSError, zipfile.BadZipFile) as exc:
                self.zip_errors.append(f"{zpath.name}: {exc}")

        prefix = _detect_prefix([p for p, _ in raw])
        if len(prefix) >= 2:
            self.photos_root_name = prefix[1]
        elif prefix:
            self.photos_root_name = prefix[0]

        for parts, loc in raw:
            rest = _strip_prefix(parts, prefix)
            if not rest:
                continue
            rel = "/".join(rest)
            if rel not in self._files:
                self._files[rel] = loc

        self._overlay_loose()

    def _overlay_loose(self) -> None:
        by_orig: dict[str, list[Path]] = {}
        for item in self._dump.iterdir():
            if not item.is_file() or not is_media(item.name):
                continue
            original, _part = parse_oversize_name(item.name)
            if not original:
                original = item.name
            by_orig.setdefault(original.lower(), []).append(item)

        names_in_dir: dict[str, set[str]] = {}
        for rel in self._files:
            parent = str(PurePosixPath(rel).parent)
            if parent == ".":
                parent = ""
            names_in_dir.setdefault(parent, set()).add(PurePosixPath(rel).name)

        missing: list[str] = []
        for rel, loc in list(self._files.items()):
            name = PurePosixPath(rel).name
            media_name = sidecar_media_name(name)
            if not media_name or not is_media(media_name):
                continue
            parent = str(PurePosixPath(rel).parent)
            if parent == ".":
                parent = ""
            present = names_in_dir.get(parent, set())
            if media_name in present:
                continue
            media_rel = f"{parent}/{media_name}" if parent else media_name
            missing.append(media_rel)

        for media_rel in missing:
            fname = PurePosixPath(media_rel).name
            pool = by_orig.get(fname.lower())
            if not pool:
                continue
            src = pool[0]
            st = src.stat()
            self._files[media_rel] = _Loc(
                zip_path=None,
                inner=None,
                file_path=src,
                size=st.st_size,
                mtime=st.st_mtime,
            )

    def media_rels(self) -> list[str]:
        return sorted(r for r in self._files if is_media(r))

    def sidecar_gaps(self) -> list[str]:
        gaps: list[str] = []
        by_parent: dict[str, set[str]] = {}
        for rel in self._files:
            parent = str(PurePosixPath(rel).parent)
            if parent == ".":
                parent = ""
            by_parent.setdefault(parent, set()).add(PurePosixPath(rel).name)
        for rel in self._files:
            name = PurePosixPath(rel).name
            media_name = sidecar_media_name(name)
            if not media_name or not is_media(media_name):
                continue
            parent = str(PurePosixPath(rel).parent)
            if parent == ".":
                parent = ""
            if media_name not in by_parent.get(parent, set()):
                media_rel = f"{parent}/{media_name}" if parent else media_name
                gaps.append(media_rel)
        return gaps

    def album_metadata_count(self) -> int:
        return sum(1 for rel in self._files if PurePosixPath(rel).name.lower() == "metadata.json")

    def _norm(self, rel: str) -> str:
        parts = normalize_rel(rel)
        return "/".join(parts)

    def exists(self, rel: str) -> bool:
        key = self._norm(rel)
        if key in self._files:
            return True
        prefix = key + "/" if key else ""
        return any(n.startswith(prefix) for n in self._files)

    def stat(self, rel: str) -> FileStat:
        key = self._norm(rel)
        loc = self._files.get(key)
        if loc:
            return FileStat(size=loc.size, mtime=loc.mtime, is_dir=False)
        if self.exists(key):
            return FileStat(size=0, mtime=0.0, is_dir=True)
        raise NotFound(key or ".")

    def list(self, rel: str) -> list[str]:
        st = self.stat(rel)
        if not st.is_dir:
            raise InvalidPath("not a directory")
        key = self._norm(rel)
        prefix = key + "/" if key else ""
        names: set[str] = set()
        for name in self._files:
            if key and not name.startswith(prefix):
                continue
            rest = name[len(prefix) :] if prefix else name
            if rest:
                names.add(rest.split("/", 1)[0])
        return sorted(names)

    def walk(self) -> Iterator[str]:
        yield from sorted(self._files)

    @contextmanager
    def open_read(self, rel: str) -> Iterator[BinaryIO]:
        key = self._norm(rel)
        loc = self._files.get(key)
        if loc is None:
            raise NotFound(key)
        if loc.file_path is not None:
            handle = loc.file_path.open("rb")
            try:
                yield handle
            finally:
                handle.close()
            return
        if loc.zip_path is None or loc.inner is None:
            raise NotFound(key)
        zf = _open_zip(loc.zip_path)
        try:
            yield zf.open(loc.inner)
        finally:
            zf.close()

    def open_write(self, rel: str) -> Iterator[BinaryIO]:
        raise NotSupported("Takeout zip store is read-only")

    def delete(self, rel: str) -> None:
        raise NotSupported("Takeout zip store is read-only")
