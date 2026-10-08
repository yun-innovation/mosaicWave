"""Hierarchical originals: virtual `/` paths, host jail inside a root directory."""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Protocol

from mosaicwave.storage.errors import InvalidPath, NotFound, PermissionDenied
from mosaicwave.storage.paths import is_reserved_windows_name, join_rel, normalize_rel


@dataclass(frozen=True)
class FileStat:
    size: int
    mtime: float
    is_dir: bool


class FileStore(Protocol):
    def stat(self, rel: str) -> FileStat: ...

    def list(self, rel: str) -> list[str]: ...

    def walk(self) -> Iterator[str]: ...

    def exists(self, rel: str) -> bool: ...

    def delete(self, rel: str) -> None: ...

    def open_read(self, rel: str) -> Iterator[BinaryIO]: ...

    def open_write(self, rel: str) -> Iterator[BinaryIO]: ...


def _mtime(path: Path) -> float:
    return path.stat().st_mtime


class LocalFileStore:
    """FileStore rooted at a host directory (QNAP share, Windows folder, Linux path)."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).expanduser()
        self._root.mkdir(parents=True, exist_ok=True)
        self._resolved_root = self._root.resolve()

    def _join(self, rel: str) -> Path:
        parts = normalize_rel(rel)
        candidate = self._resolved_root.joinpath(*parts)
        # Resolve existing prefixes so junctions/symlinks cannot escape.
        try:
            resolved = candidate.resolve(strict=False)
        except OSError as exc:
            raise PermissionDenied(str(exc)) from exc
        try:
            resolved.relative_to(self._resolved_root)
        except ValueError as exc:
            raise InvalidPath("path escapes store root") from exc
        return resolved

    def _lstat_or_raise(self, path: Path) -> os.stat_result:
        try:
            return path.lstat()
        except FileNotFoundError as exc:
            raise NotFound(str(path)) from exc
        except PermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        except OSError as exc:
            raise PermissionDenied(str(exc)) from exc

    def exists(self, rel: str) -> bool:
        path = self._join(rel)
        try:
            st = path.lstat()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise PermissionDenied(str(exc)) from exc
        if stat.S_ISLNK(st.st_mode):
            return False
        return True

    def stat(self, rel: str) -> FileStat:
        path = self._join(rel)
        st = self._lstat_or_raise(path)
        if stat.S_ISLNK(st.st_mode):
            raise InvalidPath("symlinks are not followed")
        return FileStat(size=st.st_size, mtime=st.st_mtime, is_dir=stat.S_ISDIR(st.st_mode))

    def list(self, rel: str) -> list[str]:
        path = self._join(rel)
        st = self._lstat_or_raise(path)
        if stat.S_ISLNK(st.st_mode):
            raise InvalidPath("symlinks are not followed")
        if not stat.S_ISDIR(st.st_mode):
            raise InvalidPath("not a directory")
        names: list[str] = []
        try:
            for name in os.listdir(path):
                if is_reserved_windows_name(name):
                    continue
                names.append(name)
        except PermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        names.sort()
        return names

    def walk(self) -> Iterator[str]:
        root = self._resolved_root
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [d for d in dirnames if not is_reserved_windows_name(d)]
            current = Path(dirpath)
            try:
                current.resolve().relative_to(root)
            except ValueError:
                dirnames[:] = []
                continue
            for name in filenames:
                if is_reserved_windows_name(name):
                    continue
                full = current / name
                try:
                    st = full.lstat()
                except OSError:
                    continue
                if stat.S_ISLNK(st.st_mode):
                    continue
                rel = full.relative_to(root).as_posix()
                yield rel

    @contextmanager
    def open_read(self, rel: str) -> Iterator[BinaryIO]:
        path = self._join(rel)
        st = self._lstat_or_raise(path)
        if stat.S_ISLNK(st.st_mode) or stat.S_ISDIR(st.st_mode):
            raise InvalidPath("not a readable file")
        try:
            handle = path.open("rb")
        except FileNotFoundError as exc:
            raise NotFound(rel) from exc
        except PermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        try:
            yield handle
        finally:
            handle.close()

    @contextmanager
    def open_write(self, rel: str) -> Iterator[BinaryIO]:
        dest = self._join(rel)
        if dest == self._resolved_root:
            raise InvalidPath("cannot write the store root")
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix="mw-", suffix=".tmp", dir=dest.parent)
        tmp_path = Path(tmp_name)
        handle = os.fdopen(fd, "wb")
        try:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        except Exception:
            handle.close()
            tmp_path.unlink(missing_ok=True)
            raise
        handle.close()
        os.replace(tmp_path, dest)

    def delete(self, rel: str) -> None:
        path = self._join(rel)
        st = self._lstat_or_raise(path)
        try:
            if stat.S_ISDIR(st.st_mode) and not stat.S_ISLNK(st.st_mode):
                path.rmdir()
            else:
                path.unlink()
        except FileNotFoundError as exc:
            raise NotFound(rel) from exc
        except OSError as exc:
            raise PermissionDenied(str(exc)) from exc


class MemoryFileStore:
    """In-memory FileStore for pytest. Keys are virtual `/` paths."""

    def __init__(self) -> None:
        self._files: dict[str, tuple[bytes, float]] = {}

    def _now(self) -> float:
        return datetime.now(timezone.utc).timestamp()

    def _file(self, rel: str) -> tuple[bytes, float]:
        parts = normalize_rel(rel)
        key = join_rel(parts)
        if key not in self._files:
            raise NotFound(key or ".")
        return self._files[key]

    def exists(self, rel: str) -> bool:
        parts = normalize_rel(rel)
        key = join_rel(parts)
        if key == "":
            return True
        if key in self._files:
            return True
        prefix = key + "/"
        return any(k.startswith(prefix) for k in self._files)

    def stat(self, rel: str) -> FileStat:
        parts = normalize_rel(rel)
        key = join_rel(parts)
        if key == "" or any(k.startswith(key + "/") for k in self._files):
            if key in self._files:
                data, mtime = self._files[key]
                return FileStat(size=len(data), mtime=mtime, is_dir=False)
            mtimes = [self._files[k][1] for k in self._files if key == "" or k.startswith(key + "/")]
            return FileStat(size=0, mtime=max(mtimes) if mtimes else self._now(), is_dir=True)
        data, mtime = self._file(rel)
        return FileStat(size=len(data), mtime=mtime, is_dir=False)

    def list(self, rel: str) -> list[str]:
        st = self.stat(rel)
        if not st.is_dir:
            raise InvalidPath("not a directory")
        parts = normalize_rel(rel)
        prefix = join_rel(parts)
        base = prefix + "/" if prefix else ""
        names: set[str] = set()
        for key in self._files:
            if prefix:
                if key == prefix or not key.startswith(base):
                    continue
                rest = key[len(base) :]
            else:
                rest = key
            if rest:
                names.add(rest.split("/", 1)[0])
        return sorted(names)

    def walk(self) -> Iterator[str]:
        yield from sorted(self._files)

    @contextmanager
    def open_read(self, rel: str) -> Iterator[BinaryIO]:
        from io import BytesIO

        data, _mtime = self._file(rel)
        yield BytesIO(data)

    @contextmanager
    def open_write(self, rel: str) -> Iterator[BinaryIO]:
        from io import BytesIO

        parts = normalize_rel(rel)
        key = join_rel(parts)
        if not key:
            raise InvalidPath("cannot write the store root")
        buf = BytesIO()
        yield buf
        self._files[key] = (buf.getvalue(), self._now())

    def delete(self, rel: str) -> None:
        parts = normalize_rel(rel)
        key = join_rel(parts)
        if key in self._files:
            del self._files[key]
            return
        prefix = key + "/" if key else None
        if prefix and any(k.startswith(prefix) for k in self._files):
            raise PermissionDenied("directory not empty")
        raise NotFound(key or ".")
