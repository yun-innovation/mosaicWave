"""Opaque derived bytes (thumbs, later previews). Keys use `/` as a namespace."""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, Protocol

from mosaicwave.storage.errors import InvalidPath, NotFound, PermissionDenied
from mosaicwave.storage.paths import normalize_blob_key


@dataclass(frozen=True)
class BlobStat:
    size: int
    mtime: float


class BlobStore(Protocol):
    def exists(self, key: str) -> bool: ...

    def stat(self, key: str) -> BlobStat: ...

    def delete(self, key: str) -> None: ...

    @contextmanager
    def get(self, key: str) -> Iterator[BinaryIO]: ...

    def put(self, key: str, data: BinaryIO) -> None: ...


class LocalBlobStore:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).expanduser()
        self._root.mkdir(parents=True, exist_ok=True)
        self._resolved_root = self._root.resolve()

    def _path(self, key: str) -> Path:
        normalized = normalize_blob_key(key)
        parts = normalized.split("/")
        candidate = self._resolved_root.joinpath(*parts)
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self._resolved_root)
        except ValueError as exc:
            raise InvalidPath("blob key escapes store root") from exc
        return resolved

    def exists(self, key: str) -> bool:
        path = self._path(key)
        return path.is_file()

    def stat(self, key: str) -> BlobStat:
        path = self._path(key)
        try:
            st = path.lstat()
        except FileNotFoundError as exc:
            raise NotFound(key) from exc
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise NotFound(key)
        return BlobStat(size=st.st_size, mtime=st.st_mtime)

    @contextmanager
    def get(self, key: str) -> Iterator[BinaryIO]:
        path = self._path(key)
        try:
            handle = path.open("rb")
        except FileNotFoundError as exc:
            raise NotFound(key) from exc
        except PermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        try:
            yield handle
        finally:
            handle.close()

    def put(self, key: str, data: BinaryIO) -> None:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix="mw-", suffix=".tmp", dir=dest.parent)
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                while True:
                    chunk = data.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, dest)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    def delete(self, key: str) -> None:
        path = self._path(key)
        try:
            path.unlink()
        except FileNotFoundError as exc:
            raise NotFound(key) from exc
        except PermissionError as exc:
            raise PermissionDenied(str(exc)) from exc


class MemoryBlobStore:
    def __init__(self) -> None:
        self._blobs: dict[str, tuple[bytes, float]] = {}

    def exists(self, key: str) -> bool:
        return normalize_blob_key(key) in self._blobs

    def stat(self, key: str) -> BlobStat:
        k = normalize_blob_key(key)
        if k not in self._blobs:
            raise NotFound(k)
        data, mtime = self._blobs[k]
        return BlobStat(size=len(data), mtime=mtime)

    @contextmanager
    def get(self, key: str) -> Iterator[BinaryIO]:
        k = normalize_blob_key(key)
        if k not in self._blobs:
            raise NotFound(k)
        yield BytesIO(self._blobs[k][0])

    def put(self, key: str, data: BinaryIO) -> None:
        k = normalize_blob_key(key)
        self._blobs[k] = (data.read(), datetime.now(timezone.utc).timestamp())

    def delete(self, key: str) -> None:
        k = normalize_blob_key(key)
        if k not in self._blobs:
            raise NotFound(k)
        del self._blobs[k]
