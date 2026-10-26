"""Backup archive format shared by backup and restore (doc/backup.md).

A site archive is a zip with ``manifest.json``, ``library.db`` (the site database: accounts,
jobs), ``accounts/{user_id}/library.db`` (one snapshot per user's photos and albums),
``libraries/{user_id}/...`` (originals) and, optionally, ``blobs/...`` (thumbs). Databases are
consistent SQLite snapshots. Media are stored uncompressed; databases and manifest are deflated.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
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

SITE_NAME = re.compile(r"^mosaicwave-site-\d{8}-\d{6}(?:\.zip|-\d{3}\.(?:zip|big))$")
LIBRARY_NAME = re.compile(r"^mosaicwave-library-\d{8}-\d{6}(?:\.zip|-\d{3}\.(?:zip|big))$")
PART_NAME = re.compile(r"^(mosaicwave-(?:site|library)-\d{8}-\d{6})-(\d{3})\.(zip|big)$")

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
    loose_entries(manifest)
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
    loose_entries(manifest)
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


def part_name(base: str, number: int, *, loose: bool = False) -> str:
    return f"{base}-{number:03d}.{'big' if loose else 'zip'}"


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
    "loose",
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

    paths: list[Path]  # the zips, in part order
    manifest: dict  # for the whole set: counts and warnings are the sums
    manifests: list[dict]  # one per zip, in part order
    loose: list["LooseFile"] = field(default_factory=list)  # the big files beside the zips


def read_manifest_file(path: Path) -> dict:
    try:
        with zipfile.ZipFile(path) as zf:
            return read_manifest(zf)
    except zipfile.BadZipFile as exc:
        raise ArchiveError(f"not a zip file or damaged: {exc}") from exc


def open_set(path: Path, check: Callable[[dict], None]) -> ArchiveSet:
    """The archive ``path`` belongs to. For a part, every sibling must be there and match.

    ``check`` validates a manifest (kind, format, schema). Raises ``PartsMissing`` listing the part
    numbers that are not next to ``path``. ``path`` may be a big file (``-NNN.big``): the set is then
    opened through a zip of the same backup beside it.
    """
    named = PART_NAME.match(path.name)
    if named and named.group(3) == "big":
        zips = sorted(path.parent.glob(f"{named.group(1)}-???.zip"))
        if not zips:
            raise ArchiveError("none of the zips of this backup is next to that file")
        path = zips[0]
    first = read_manifest_file(path)
    check(first)
    info = part_info(first)
    if info is None:
        return ArchiveSet([path], first, [first])
    part, parts = info
    base = part_base(path, part)
    listed = loose_entries(first)
    big = {e.part: e for e in listed}
    missing = []
    for n in range(1, parts + 1):
        here = path.with_name(part_name(base, n, loose=n in big))
        if not here.is_file():
            missing.append(n)
    if missing:
        raise PartsMissing(missing, parts)
    loose: list[LooseFile] = []
    for entry in listed:
        file = path.with_name(part_name(base, entry.part, loose=True))
        if file.stat().st_size != entry.size:
            raise ArchiveError(f"part {entry.part} ({file.name}) is not the size the backup says")
        loose.append(LooseFile(entry, file))
    numbers = [n for n in range(1, parts + 1) if n not in big]
    paths = [path.with_name(part_name(base, n)) for n in numbers]
    manifests: list[dict] = []
    for n, p in zip(numbers, paths):
        m = first if n == part else read_manifest_file(p)
        if n != part:
            check(m)
        if part_info(m) != (n, parts) or any(m.get(k) != first.get(k) for k in _SAME_IN_EVERY_PART):
            raise ArchiveError(f"part {n} belongs to a different backup")
        manifests.append(m)
    return ArchiveSet(paths, merge_manifests(manifests), manifests, loose)


# --- big files: copied beside the zips, not cut and not zipped -------------------------------------
#
# A file bigger than a part (a long video, a large database) is not put in a zip. As with an export it
# is copied as a plain file beside the zips, named ``<base>-NNN.big`` after the part number it takes
# (the zips are 001 to Z, the big files follow). The manifests of the zips say which number is which
# file: name inside the backup, size and SHA-256, so restore finds and checks it.

_COPY = 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class LooseEntry:
    """One big file as the manifests list it."""

    part: int
    name: str
    size: int
    sha256: str
    mtime: int


@dataclass
class LooseFile:
    """A big file of a set that is on disk."""

    entry: LooseEntry
    path: Path


def loose_entries(manifest: dict) -> list[LooseEntry]:
    """The big files a manifest lists (validated). None for an ordinary archive."""
    raw = manifest.get("loose")
    if raw is None:
        return []
    info = part_info(manifest)
    if info is None or not isinstance(raw, list):
        raise ArchiveError("manifest has a list of big files it cannot have")
    found: list[LooseEntry] = []
    numbers: set[int] = set()
    names: set[str] = set()
    for item in raw:
        try:
            part, name, size = item["part"], item["name"], item["size"]
            sha, mtime = item["sha256"], item["mtime"]
        except (KeyError, TypeError) as exc:
            raise ArchiveError("manifest lists a big file without all its facts") from exc
        if (
            not isinstance(part, int)
            or isinstance(part, bool)
            or not 2 <= part <= info[1]
            or part in numbers
            or not isinstance(name, str)
            or name == MANIFEST
            or name in names
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or not isinstance(sha, str)
            or not _SHA256.match(sha)
            or not isinstance(mtime, int)
            or isinstance(mtime, bool)
        ):
            raise ArchiveError("manifest lists an invalid big file")
        safe_member(name)
        numbers.add(part)
        names.add(name)
        found.append(LooseEntry(part, name, size, sha, mtime))
    return sorted(found, key=lambda e: e.part)


def big_media(manifest: dict) -> int:
    """How many photos and videos sit beside the zips; the first zip counts them in its ``file_count``."""
    info = part_info(manifest)
    if info is None or info[0] != 1:
        return 0
    return sum(is_media_name(e.name) for e in loose_entries(manifest))


def hash_loose(loose: LooseFile) -> str:
    digest = hashlib.sha256()
    with open(loose.path, "rb") as src:
        while block := src.read(_COPY):
            digest.update(block)
    return digest.hexdigest()


def copy_loose(loose: LooseFile, dest: Path) -> None:
    """Copy a big file to ``dest`` and check it against the manifest (its size and SHA-256)."""
    digest = hashlib.sha256()
    written = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(loose.path, "rb") as src, open(dest, "wb") as out:
        while block := src.read(_COPY):
            digest.update(block)
            out.write(block)
            written += len(block)
    if written != loose.entry.size or digest.hexdigest() != loose.entry.sha256:
        dest.unlink(missing_ok=True)
        raise ArchiveError(f"{loose.path.name} is damaged: it does not match the backup")
    if is_media_name(loose.entry.name):
        try:
            os.utime(dest, (loose.entry.mtime, loose.entry.mtime))
        except OSError:
            pass


def is_media_name(name: str) -> bool:
    """A photo or thumbnail entry (the rest are the databases)."""
    return name.startswith((f"{LIBRARIES}/", f"{BLOBS}/"))


@dataclass(frozen=True)
class Piece:
    """One file to put in a zip."""

    path: Path
    arcname: str
    deflate: bool = False  # databases are compressed, photos and videos are stored as they are


@dataclass
class Plan:
    groups: list[list[Piece]]  # what goes in each zip
    loose: list[tuple[Path, str, int]]  # big files: path, name inside the backup, size


def _overhead(name: str) -> int:
    """What an entry costs in a zip beyond its data: local and central headers, zip64 fields."""
    return 2 * len(name.encode("utf-8")) + 200


def deflated_size(path: Path) -> int:
    """How many bytes ``path`` takes in a zip when deflated (what ``ZipFile`` does by default)."""
    packer = zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, -15)
    packed = 0
    with open(path, "rb") as src:
        while block := src.read(_COPY):
            packed += len(packer.compress(block))
    return packed + len(packer.flush())


def plan_parts(
    files: list[tuple[Path, str, int]], limit: int | None, stored: dict[str, int] | None = None
) -> Plan:
    """Share ``files`` out over zips that stay within ``limit`` bytes (None: one zip).

    A file that fits is stored whole in a zip. A file bigger than a zip is left out of the zips and
    copied beside them (``Plan.loose``), a database as much as a photo. Zip headers and a reserve for
    the manifest are counted, so a zip is not bigger than ``limit`` unless a file grew meanwhile.
    What counts against a zip is the size a file takes in it: a photo or video is stored as it is, a
    database is deflated, so ``stored`` gives its deflated size by name (see ``deflated_size``). A file
    that fits no more room in the current zip but is smaller than a zip starts the next zip; one whose
    stored size is bigger than a zip is left out of the zips and copied as it is (its real size).
    """
    stored = stored or {}
    if not limit:
        return Plan([[Piece(path, arcname, not is_media_name(arcname)) for path, arcname, _ in files]], [])
    reserve = min(_COPY, limit // 10)
    cap = max(limit - reserve, 1)
    groups: list[list[Piece]] = [[]]
    loose: list[tuple[Path, str, int]] = []
    used = 0
    for path, arcname, size in files:
        cost = stored.get(arcname, size) + _overhead(arcname)
        if cost > cap:
            loose.append((path, arcname, size))
            continue
        if used + cost > cap:
            groups.append([])
            used = 0
        groups[-1].append(Piece(path, arcname, not is_media_name(arcname)))
        used += cost
    if len(groups) + len(loose) > MAX_PARTS:
        raise ArchiveError(
            f"a part size this small would need {len(groups) + len(loose)} files (at most {MAX_PARTS})"
        )
    return Plan(groups, loose)


def _copy_big(src: Path, dest: Path, size: int) -> str:
    """Copy a big file; returns its SHA-256. A file that changed size meanwhile fails the backup."""
    digest = hashlib.sha256()
    written = 0
    with open(src, "rb") as source, open(dest, "wb") as out:
        while block := source.read(_COPY):
            digest.update(block)
            out.write(block)
            written += len(block)
    if written != size:
        raise ArchiveError("a large file changed while it was being backed up; try again")
    return digest.hexdigest()


def write_archive(
    out_dir: Path,
    base: str,
    plan: Plan,
    common: dict,
    set_warnings: list[str],
    on_progress: Callable[[int, int, str], None] | None = None,
) -> tuple[list[Path], dict]:
    """Write the zip, or the zips and big files of a split backup, named after ``base``.

    Returns (every file in part order, the manifest of the whole set). Files are written as ``*.part``
    and renamed at the end, the big files first and the last zip before the first, so a first zip that
    exists means everything exists and a crash never leaves something that looks finished.
    """
    groups = plan.groups
    temps: list[Path] = []
    published: list[Path] = []
    manifests: list[dict] = []
    done = 0
    total = sum(len(g) for g in groups) + len(plan.loose)
    try:
        # The big files first: their size and checksum go into every zip's manifest.
        copied: list[tuple[LooseEntry, Path, Path]] = []  # entry, temp file, final file
        vanished_big = 0
        for path, arcname, size in plan.loose:
            number = len(groups) + len(copied) + 1
            final = out_dir / part_name(base, number, loose=True)
            tmp = final.with_name(final.name + ".part")
            tmp.unlink(missing_ok=True)
            temps.append(tmp)
            try:
                sha = _copy_big(path, tmp, size)
                mtime = int(path.stat().st_mtime)
            except FileNotFoundError:
                tmp.unlink(missing_ok=True)
                temps.remove(tmp)
                vanished_big += is_media_name(arcname)
            else:
                copied.append((LooseEntry(number, arcname, size, sha, mtime), tmp, final))
            done += 1
            if on_progress is not None:
                on_progress(done, total, arcname)
        count = len(groups) + len(copied)
        big_media = [e for e, _, _ in copied if is_media_name(e.name)]
        if copied:
            common = {
                **common,
                "loose": [
                    {"part": e.part, "name": e.name, "size": e.size, "sha256": e.sha256, "mtime": e.mtime}
                    for e, _, _ in copied
                ],
            }
        finals: list[Path] = []
        for index, group in enumerate(groups):
            final = out_dir / (f"{base}.zip" if count == 1 else part_name(base, index + 1))
            tmp = final.with_name(final.name + ".part")
            tmp.unlink(missing_ok=True)
            temps.append(tmp)
            finals.append(final)
            written = len(big_media) if index == 0 else 0  # the big files are counted in the first zip
            total_bytes = sum(e.size for e in big_media) if index == 0 else 0
            vanished = vanished_big if index == 0 else 0
            with zipfile.ZipFile(tmp, "w", allowZip64=True, strict_timestamps=False) as zf:
                for piece in group:
                    try:
                        kind = zipfile.ZIP_DEFLATED if piece.deflate else zipfile.ZIP_STORED
                        zf.write(piece.path, piece.arcname, compress_type=kind)
                        if is_media_name(piece.arcname):  # the databases are not counted
                            total_bytes += zf.getinfo(piece.arcname).file_size
                            written += 1
                    except FileNotFoundError:
                        vanished += is_media_name(piece.arcname)
                    done += 1
                    if on_progress is not None:
                        on_progress(done, total, piece.arcname)
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
        for _entry, tmp, final in copied:
            os.replace(tmp, final)
            published.append(final)
        for tmp, final in reversed(list(zip(temps[len(copied) :], finals))):
            os.replace(tmp, final)
            published.append(final)
    except BaseException:
        for tmp in temps:
            tmp.unlink(missing_ok=True)
        for final in published:
            final.unlink(missing_ok=True)
        raise
    paths = finals + [final for _, _, final in copied]
    return paths, (merge_manifests(manifests) if len(paths) > 1 else manifests[0])
