"""Backup archive format shared by backup and restore (doc/backup.md).

A site archive is a zip with ``manifest.json``, ``library.db`` (the site database: accounts,
jobs), ``accounts/{user_id}/library.db`` (one snapshot per user's photos and albums),
``libraries/{user_id}/...`` (originals) and, optionally, ``blobs/...`` (thumbs). Databases are
consistent SQLite snapshots. Media are stored uncompressed; databases and manifest are deflated.
"""

from __future__ import annotations

import json
import re
import zipfile
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

from mosaicwave.library.dbs import USER_ID
from mosaicwave.schema.split import SPLIT_VERSION

FORMAT = 1
KIND_SITE = "site"
MANIFEST = "manifest.json"
DB_ENTRY = "library.db"
ACCOUNTS = "accounts"
LIBRARIES = "libraries"
BLOBS = "blobs"

# source.root_uri inside an archive: this prefix + a path relative to the data folder, so
# a restore on another host or OS rebuilds the real path.
DATA_PREFIX = "mw-data:"

SITE_NAME = re.compile(r"^mosaicwave-site-\d{8}-\d{6}\.zip$")

_SKIP_NAMES = {".DS_Store", ".localized", "Thumbs.db", "desktop.ini"}
_DRIVE = re.compile(r"^[A-Za-z]:")


class ArchiveError(ValueError):
    """The archive (or the request to make or restore one) cannot be used. English text."""


def account_entry(user_id: str) -> str:
    return f"{ACCOUNTS}/{user_id}/{DB_ENTRY}"


def skip_name(name: str) -> bool:
    return name in _SKIP_NAMES or name.startswith("._")


def iter_files(root: Path, prefix: str) -> Iterator[tuple[Path, str]]:
    """Regular files under ``root`` with their archive name. Symlinks are left out."""
    if not root.is_dir():
        return
    stack = [root]
    while stack:
        folder = stack.pop()
        try:
            entries = sorted(folder.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for entry in entries:
            if skip_name(entry.name) or entry.is_symlink():
                continue
            if entry.is_dir():
                stack.append(entry)
            elif entry.is_file():
                rel = PurePosixPath(*entry.relative_to(root).parts)
                yield entry, f"{prefix}/{rel}"


def safe_member(name: str) -> str:
    """Return ``name`` if it may be extracted, else raise."""
    if name in (MANIFEST, DB_ENTRY):
        return name
    if "\\" in name or name.startswith("/"):
        raise ArchiveError(f"unsafe name in archive: {name!r}")
    parts = PurePosixPath(name).parts
    if len(parts) == 3 and parts[0] == ACCOUNTS and parts[2] == DB_ENTRY and USER_ID.match(parts[1]):
        return name
    if len(parts) < 2 or parts[0] not in (LIBRARIES, BLOBS) or any(p in ("", ".", "..") for p in parts):
        raise ArchiveError(f"unexpected name in archive: {name!r}")
    if any(_DRIVE.match(p) for p in parts):
        raise ArchiveError(f"unsafe name in archive: {name!r}")
    return name


def read_manifest(zf: zipfile.ZipFile) -> dict:
    try:
        raw = zf.read(MANIFEST)
    except KeyError as exc:
        raise ArchiveError("not a mosaicWave archive (no manifest.json)") from exc
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchiveError("manifest.json is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise ArchiveError("manifest.json is not an object")
    return manifest


def check_manifest(manifest: dict, site_supported: int, library_supported: int) -> None:
    fmt = manifest.get("format")
    if fmt != FORMAT:
        raise ArchiveError(f"archive format {fmt!r} is not supported (this app reads format {FORMAT})")
    if manifest.get("kind") != KIND_SITE:
        raise ArchiveError(f"archive kind {manifest.get('kind')!r} is not a site backup")
    site = manifest.get("schema_version")
    if not isinstance(site, int) or site < SPLIT_VERSION:
        raise ArchiveError("manifest has no valid schema_version")
    library = manifest.get("library_schema_version")
    if not isinstance(library, int) or library < 1:
        raise ArchiveError("manifest has no valid library_schema_version")
    for label, found, supported in (("site", site, site_supported), ("library", library, library_supported)):
        if found > supported:
            raise ArchiveError(
                f"archive {label} database is schema v{found}; this mosaicWave supports up to "
                f"v{supported}. Install a newer mosaicWave to restore it."
            )
    users = manifest.get("user_ids")
    if not isinstance(users, list) or not all(isinstance(u, str) and USER_ID.match(u) for u in users):
        raise ArchiveError("manifest has no valid user_ids")
