"""Virtual paths for FileStore (always `/` separators in the API)."""

from __future__ import annotations

import re

from mosaicwave.storage.errors import InvalidPath

# Windows device names (any extension). Walk skips these; open/stat raise InvalidPath.
_RESERVED_STEMS = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)

_BLOB_KEY_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")
_SMB_ILLEGAL = str.maketrans({ch: "_" for ch in '<>:"/\\|?*'})


def is_reserved_windows_name(name: str) -> bool:
    stem = name.split(".", 1)[0].upper().rstrip(" .")
    return stem in _RESERVED_STEMS


def safe_library_filename(name: str) -> str:
    """Basename that SMB/Windows can store (Takeout names often contain `:`)."""
    text = name.replace("\\", "/").rsplit("/", 1)[-1].replace("\x00", "")
    text = text.translate(_SMB_ILLEGAL).rstrip(" .")
    if not text or text in (".", ".."):
        text = "file"
    if is_reserved_windows_name(text):
        if "." in text:
            stem, _, suffix = text.rpartition(".")
            text = f"{stem}_file.{suffix}"
        else:
            text = f"{text}_file"
    return text


def normalize_rel(rel: str) -> tuple[str, ...]:
    """Return path parts under the store root. Empty tuple is the root."""
    if rel is None:
        raise InvalidPath("path is required")
    if "\x00" in rel:
        raise InvalidPath("NUL in path")
    text = rel.replace("\\", "/").strip()
    if text in ("", "/"):
        return ()
    text = text.strip("/")
    parts: list[str] = []
    for part in text.split("/"):
        if part in ("", ".", ".."):
            raise InvalidPath(f"illegal path segment: {part!r}")
        if is_reserved_windows_name(part):
            raise InvalidPath(f"reserved name: {part}")
        parts.append(part)
    return tuple(parts)


def join_rel(parts: tuple[str, ...]) -> str:
    return "/".join(parts)


def normalize_blob_key(key: str) -> str:
    if key is None or not key:
        raise InvalidPath("blob key is required")
    if "\x00" in key:
        raise InvalidPath("NUL in blob key")
    if "\\" in key or key.startswith("/") or key.endswith("/"):
        raise InvalidPath("blob key must use interior `/` only")
    parts = key.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise InvalidPath("illegal blob key segment")
    for part in parts:
        if not _BLOB_KEY_SEGMENT.fullmatch(part):
            raise InvalidPath(f"illegal blob key segment: {part!r}")
    return "/".join(parts)
