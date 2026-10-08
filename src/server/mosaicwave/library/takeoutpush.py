from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import BinaryIO

from mosaicwave.storage.errors import InvalidPath
from mosaicwave.storage.paths import is_reserved_windows_name, normalize_rel

# Takeout zip splits are often 2/4/10/50GB; leftovers beside the zips are larger than that split.
MAX_PUSH_BYTES = 256 * 1024 * 1024 * 1024
_WIN_BAD = re.compile(r'[<>:"/\\|?*]')


def staging_dir(data_dir: Path, job_id: str) -> Path:
    return nt_path(data_dir / "tmp" / "takeout-push" / job_id)


def _plain_nt(path: Path) -> str:
    raw = str(path)
    if raw.startswith("\\\\?\\UNC\\"):
        return "\\\\" + raw[8:]
    if raw.startswith("\\\\?\\"):
        return raw[4:]
    return raw


def nt_path(path: Path) -> Path:
    """Use the Windows long-path prefix so Takeout trees can exceed MAX_PATH."""
    resolved = Path(_plain_nt(path)).expanduser()
    try:
        resolved = resolved.resolve()
    except OSError:
        resolved = resolved.absolute()
    if os.name != "nt":
        return resolved
    raw = str(resolved)
    if raw.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + raw[2:])
    return Path("\\\\?\\" + raw)


def _fs_part(part: str) -> str:
    if os.name != "nt":
        return part
    cleaned = _WIN_BAD.sub("_", part).rstrip(" .")
    if not cleaned or is_reserved_windows_name(cleaned):
        cleaned = f"_{cleaned}" if cleaned else "_unnamed"
    return cleaned


def _is_under(path: Path, parent: Path) -> bool:
    child = os.path.normcase(os.path.abspath(_plain_nt(path)))
    root = os.path.normcase(os.path.abspath(_plain_nt(parent)))
    return child == root or child.startswith(root + os.sep)


def safe_staging_file(staging: Path, relative_path: str) -> Path:
    staging = nt_path(Path(staging))
    parts = normalize_rel(relative_path)
    if not parts:
        raise InvalidPath("relative_path is required")
    dest = nt_path(staging.joinpath(*(_fs_part(p) for p in parts)))
    if not _is_under(dest, staging):
        raise InvalidPath("relative_path escapes staging")
    return dest


def _prepare_part(dest: Path) -> tuple[Path, Path]:
    dest = nt_path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest, dest.with_name(dest.name + ".part")


def _write_chunk(out: BinaryIO, chunk: bytes | str, written: int, max_bytes: int) -> int:
    if isinstance(chunk, str):
        chunk = chunk.encode("utf-8")
    written += len(chunk)
    if written > max_bytes:
        raise ValueError(f"file too large ({written} bytes; max is {max_bytes})")
    out.write(chunk)
    return written


def write_upload(dest: Path, stream: BinaryIO, *, max_bytes: int = MAX_PUSH_BYTES) -> int:
    dest, tmp = _prepare_part(dest)
    written = 0
    try:
        with tmp.open("wb") as out:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                written = _write_chunk(out, chunk, written, max_bytes)
        tmp.replace(dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return written


async def write_upload_async(
    dest: Path, chunks: AsyncIterator[bytes], *, max_bytes: int = MAX_PUSH_BYTES
) -> int:
    dest, tmp = _prepare_part(dest)
    written = 0
    try:
        with tmp.open("wb") as out:
            async for chunk in chunks:
                if not chunk:
                    continue
                written = _write_chunk(out, chunk, written, max_bytes)
        tmp.replace(dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return written
