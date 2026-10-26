"""Backup archive format shared by backup and restore (doc/backup.md).

A site archive is a zip with ``manifest.json``, ``library.db`` (the site database: accounts,
jobs), ``accounts/{user_id}/library.db`` (one snapshot per user's photos and albums),
``libraries/{user_id}/...`` (originals) and, optionally, ``blobs/...`` (thumbs). Databases are
consistent SQLite snapshots. Media are stored uncompressed; databases and manifest are deflated.
"""

from __future__ import annotations

import json
import os
import re
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from mosaicwave.library.dbs import USER_ID
from mosaicwave.schema.split import SPLIT_VERSION

FORMAT = 1
# A backup too big for one file is a set of zips (parts). Each part is a complete zip with its own
# manifest; format 2 tells an app that predates sets to refuse a single part instead of restoring
# part of a library.
FORMAT_SET = 2
MAX_PARTS = 999
KIND_SITE = "site"
KIND_LIBRARY = "library"
MANIFEST = "manifest.json"
DB_ENTRY = "library.db"
ACCOUNTS = "accounts"
LIBRARIES = "libraries"
BLOBS = "blobs"

# source.root_uri inside an archive: this prefix + a path relative to the data folder, so
# a restore on another host or OS rebuilds the real path.
DATA_PREFIX = "mw-data:"

SITE_NAME = re.compile(r"^mosaicwave-site-\d{8}-\d{6}(?:-\d{3})?\.zip$")
LIBRARY_NAME = re.compile(r"^mosaicwave-library-\d{8}-\d{6}(?:-\d{3})?\.zip$")
PART_NAME = re.compile(r"^(mosaicwave-(?:site|library)-\d{8}-\d{6})-(\d{3})\.zip$")

_SKIP_NAMES = {".DS_Store", ".localized", "Thumbs.db", "desktop.ini"}
_DRIVE = re.compile(r"^[A-Za-z]:")


class ArchiveError(ValueError):
    """The archive (or the request to make or restore one) cannot be used. English text."""


class WrongKind(ArchiveError):
    """A site archive where a library archive is needed, or the other way round."""


class PartsMissing(ArchiveError):
    """A split backup with parts that are not there. ``missing`` are part numbers, ``parts`` the total."""

    def __init__(self, missing: list[int], parts: int) -> None:
        self.missing = missing
        self.parts = parts
        which = ", ".join(str(n) for n in missing)
        super().__init__(f"this backup has {parts} parts and part {which} is missing")


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
    if fmt not in (FORMAT, FORMAT_SET):
        raise ArchiveError(f"archive format {fmt!r} is not supported (this app reads format {FORMAT_SET})")
    part_info(manifest)
    if manifest.get("kind") != KIND_SITE:
        raise WrongKind(f"archive kind {manifest.get('kind')!r} is not a site backup")
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


def check_library_manifest(manifest: dict, library_supported: int) -> None:
    """Manifest of a single-library archive (``kind: library``)."""
    fmt = manifest.get("format")
    if fmt not in (FORMAT, FORMAT_SET):
        raise ArchiveError(f"archive format {fmt!r} is not supported (this app reads format {FORMAT_SET})")
    part_info(manifest)
    if manifest.get("kind") != KIND_LIBRARY:
        raise WrongKind(f"archive kind {manifest.get('kind')!r} is not a library backup")
    library = manifest.get("library_schema_version")
    if not isinstance(library, int) or library < 1:
        raise ArchiveError("manifest has no valid library_schema_version")
    if library > library_supported:
        raise ArchiveError(
            f"archive library database is schema v{library}; this mosaicWave supports up to "
            f"v{library_supported}. Install a newer mosaicWave to restore it."
        )
    user = manifest.get("user_id")
    if not isinstance(user, str) or not USER_ID.match(user):
        raise ArchiveError("manifest has no valid user_id")
    for key in ("assets", "albums", "file_count"):
        if not isinstance(manifest.get(key), int):
            raise ArchiveError(f"manifest has no valid {key}")



# --- split backups: a set of parts ---------------------------------------------------------------


def part_name(base: str, number: int) -> str:
    return f"{base}-{number:03d}.zip"


def part_info(manifest: dict) -> tuple[int, int] | None:
    """``(part, parts)`` of a manifest, or None for an ordinary single-zip archive."""
    fmt = manifest.get("format")
    has = "part" in manifest or "parts" in manifest
    if fmt == FORMAT:
        if has:
            raise ArchiveError("manifest of a single archive has part numbers")
        return None
    if fmt != FORMAT_SET:
        return None
    part, parts = manifest.get("part"), manifest.get("parts")
    if (
        not isinstance(part, int)
        or not isinstance(parts, int)
        or isinstance(part, bool)
        or isinstance(parts, bool)
        or not 1 <= part <= parts <= MAX_PARTS
        or parts < 2
    ):
        raise ArchiveError("manifest has no valid part numbers")
    return part, parts


def part_base(path: Path, part: int) -> str:
    """The shared name of a set's parts, taken from one part's file name (which must say its number)."""
    found = PART_NAME.match(path.name)
    if not found:
        raise ArchiveError("a part of a split backup must keep its file name (…-001.zip)")
    if int(found.group(2)) != part:
        raise ArchiveError(f"the file name says part {int(found.group(2))} but the archive is part {part}")
    return found.group(1)


_SAME_IN_EVERY_PART = (
    "kind",
    "created_at",
    "app_version",
    "include_thumbs",
    "user_ids",
    "user_id",
    "users",
    "libraries",
    "schema_version",
    "library_schema_version",
    "assets",
    "albums",
)


def merge_manifests(manifests: list[dict]) -> dict:
    """One manifest for a whole set: the shared facts, with counts and warnings added up."""
    merged = {k: v for k, v in manifests[0].items() if k != "part"}
    merged["file_count"] = sum(int(m.get("file_count", 0)) for m in manifests)
    merged["total_bytes"] = sum(int(m.get("total_bytes", 0)) for m in manifests)
    seen: list[str] = []
    for m in manifests:
        for warning in m.get("warnings", []):
            if warning not in seen:
                seen.append(str(warning))
    merged["warnings"] = seen
    return merged


@dataclass
class ArchiveSet:
    """An archive as restore sees it: one zip, or every part of a split backup."""

    paths: list[Path]
    manifest: dict  # for the whole set: counts and warnings are the sums
    manifests: list[dict]  # one per file, in part order


def read_manifest_file(path: Path) -> dict:
    try:
        with zipfile.ZipFile(path) as zf:
            return read_manifest(zf)
    except zipfile.BadZipFile as exc:
        raise ArchiveError(f"not a zip file or damaged: {exc}") from exc


def open_set(path: Path, check: Callable[[dict], None]) -> ArchiveSet:
    """The archive ``path`` belongs to. For a part, every sibling must be there and match.

    ``check`` validates a manifest (kind, format, schema). Raises ``PartsMissing`` listing the part
    numbers that are not next to ``path``.
    """
    first = read_manifest_file(path)
    check(first)
    info = part_info(first)
    if info is None:
        return ArchiveSet([path], first, [first])
    part, parts = info
    base = part_base(path, part)
    paths = [path.with_name(part_name(base, n)) for n in range(1, parts + 1)]
    missing = [n for n, p in enumerate(paths, start=1) if not p.is_file()]
    if missing:
        raise PartsMissing(missing, parts)
    manifests: list[dict] = []
    for n, p in enumerate(paths, start=1):
        m = first if n == part else read_manifest_file(p)
        if n != part:
            check(m)
        if part_info(m) != (n, parts) or any(m.get(k) != first.get(k) for k in _SAME_IN_EVERY_PART):
            raise ArchiveError(f"part {n} belongs to a different backup")
        manifests.append(m)
    return ArchiveSet(paths, merge_manifests(manifests), manifests)


def plan_parts(
    files: list[tuple[Path, str, int]], limit: int | None, first_extra: int = 0
) -> list[list[tuple[Path, str]]]:
    """Share ``files`` out over parts of about ``limit`` bytes (None: one part).

    A file is never cut: one bigger than the limit gets a part to itself. ``first_extra`` is what the
    first part already holds (the databases). The size is a target, not a promise: zip headers and a
    file that changed size meanwhile are not counted.
    """
    groups: list[list[tuple[Path, str]]] = [[]]
    used = first_extra
    for path, arcname, size in files:
        if limit and (groups[-1] or used) and used + size > limit:
            groups.append([])
            used = 0
        groups[-1].append((path, arcname))
        used += size
    if len(groups) > MAX_PARTS:
        raise ArchiveError(f"a part size this small would need {len(groups)} parts (at most {MAX_PARTS})")
    return groups


def write_archive(
    out_dir: Path,
    base: str,
    groups: list[list[tuple[Path, str]]],
    db_files: list[tuple[Path, str]],
    common: dict,
    set_warnings: list[str],
    on_progress: Callable[[int, int, str], None] | None = None,
) -> tuple[list[Path], dict]:
    """Write the zip, or the zips of a split backup, named after ``base``. Returns (paths, manifest).

    Parts are written as ``*.part`` and renamed at the end, the last part first, so a first part that
    exists means every part exists and a crash never leaves something that looks finished. The
    databases go into the first part. The returned manifest is for the whole set.
    """
    count = len(groups)
    total = sum(len(g) for g in groups)
    done = 0
    temps: list[Path] = []
    finals: list[Path] = []
    published: list[Path] = []
    manifests: list[dict] = []
    try:
        for index, group in enumerate(groups):
            final = out_dir / (f"{base}.zip" if count == 1 else part_name(base, index + 1))
            tmp = final.with_name(final.name + ".part")
            tmp.unlink(missing_ok=True)
            temps.append(tmp)
            finals.append(final)
            written = 0
            total_bytes = 0
            vanished = 0
            with zipfile.ZipFile(tmp, "w", allowZip64=True, strict_timestamps=False) as zf:
                if index == 0:
                    for db_file, arcname in db_files:
                        zf.write(db_file, arcname, compress_type=zipfile.ZIP_DEFLATED)
                for path, arcname in group:
                    try:
                        zf.write(path, arcname, compress_type=zipfile.ZIP_STORED)
                        written += 1
                        total_bytes += zf.getinfo(arcname).file_size
                    except FileNotFoundError:
                        vanished += 1
                    done += 1
                    if on_progress is not None:
                        on_progress(done, total, arcname)
                warnings = list(set_warnings)
                if vanished:
                    warnings.append(f"{vanished} file(s) disappeared while the backup ran")
                manifest = {
                    **common,
                    "format": FORMAT_SET if count > 1 else FORMAT,
                    "file_count": written,
                    "total_bytes": total_bytes,
                    "warnings": warnings,
                }
                if count > 1:
                    manifest["part"] = index + 1
                    manifest["parts"] = count
                zf.writestr(MANIFEST, json.dumps(manifest, indent=2, sort_keys=True), zipfile.ZIP_DEFLATED)
            manifests.append(manifest)
        for tmp, final in reversed(list(zip(temps, finals))):
            os.replace(tmp, final)
            published.append(final)
    except BaseException:
        for tmp in temps:
            tmp.unlink(missing_ok=True)
        for final in published:
            final.unlink(missing_ok=True)
        raise
    return finals, (merge_manifests(manifests) if count > 1 else manifests[0])