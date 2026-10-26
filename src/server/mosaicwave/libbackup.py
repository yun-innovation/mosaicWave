"""One user's library: back it up, and restore it over their live library (doc/backup.md).

A library archive (``kind: library``, format 1) is a zip with ``manifest.json``, ``library.db`` (that
user's database, source paths made portable) and ``libraries/{user_id}/...`` (the originals). No
thumbnails: they are rebuilt when photos are first viewed.

Restore is an exact swap of that one library while the API runs. It is staged first (nothing live is
touched), then the user's database engine is closed, the live database and originals are moved aside
and the staged ones moved in, with a rollback if any step fails. Once the new library is in place the
old one is deleted: nothing restores from it, and a user who wants to go back makes a backup first. A small journal makes a crash in the
middle recoverable at the next start. Other users are never touched.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from mosaicwave import __version__
from mosaicwave.archive import (
    DB_ENTRY,
    KIND_LIBRARY,
    LIBRARIES,
    MANIFEST,
    ArchiveError,
    ArchiveSet,
    LooseFile,
    big_media,
    check_library_manifest,
    copy_loose,
    hash_loose,
    iter_files,
    open_set,
    deflated_size,
    plan_parts,
    read_manifest_file,
    safe_member,
    write_archive,
)
from mosaicwave.backup import _snapshot_library
from mosaicwave.config import Settings, _checkpoint_library_db, ensure_data_dir_exists
from mosaicwave.library.dbs import USER_ID, LibraryDbs
from mosaicwave.restore import (
    _prepare_library,
    _remove,
    _require_space,
    live_max_rev,
    offset_revs,
)
from mosaicwave.schema.library import LIBRARY_CURRENT_VERSION

Progress = Callable[[int, int, str], None]

USER_STAGE_PREFIX = ".restore-staging-user-"
USER_DATA_STAGE_PREFIX = ".restore-staging-userdata-"
JOURNAL_PREFIX = "library-swap-"
ASIDE = "before-restore"
# How long a swap waits for a request that still has the user's file open (Windows cannot rename it).
SWAP_WAIT_SECONDS = 10.0


@dataclass(frozen=True)
class LibraryBackupResult:
    path: Path  # the archive, or the first part of a split backup
    manifest: dict  # for the whole backup
    paths: list[Path] = field(default_factory=list)


@dataclass
class StagedLibrary:
    manifest: dict
    stamp: str
    user_id: str
    db_stage: Path
    data_stage: Path
    upgrade_from: int | None = None


def user_backup_dir(settings: Settings, user_id: str) -> Path:
    """Where one user's archives live: under ``backups/users/``, apart from the site archives."""
    return settings.backup_dir / "users" / user_id


def library_archive_name(now: datetime | None = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    return f"mosaicwave-library-{stamp}.zip"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


# --- backup ------------------------------------------------------------------------------------


def backup_library(
    settings: Settings,
    user_id: str,
    out_dir: Path,
    *,
    split_bytes: int | None = None,
    on_progress: Progress | None = None,
) -> LibraryBackupResult:
    if not USER_ID.match(user_id or ""):
        raise ArchiveError(f"not a valid user id: {user_id!r}")
    if not (settings.accounts_dir / user_id / DB_ENTRY).is_file():
        raise ArchiveError("this library has nothing to back up yet")
    out_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).replace(microsecond=0)  # one clock for the name and the manifest
    base = library_archive_name(started)[: -len(".zip")]
    work = Path(tempfile.mkdtemp(prefix="mosaicwave-libbackup-"))
    try:
        files: list[tuple[Path, str]] = []
        skipped = 0
        for path, arcname in iter_files(settings.data_dir / LIBRARIES / user_id, f"{LIBRARIES}/{user_id}"):
            try:
                safe_member(arcname)
            except ArchiveError:
                skipped += 1
                continue
            files.append((path, arcname))
        sized: list[tuple[Path, str, int]] = []
        for path, arcname in files:
            try:
                sized.append((path, arcname, path.stat().st_size))
            except OSError:
                sized.append((path, arcname, 0))
        _require_space(out_dir, sum(size for _, _, size in sized))

        db_file, facts, outside = _snapshot_library(settings, user_id, work, dest=work / DB_ENTRY)
        warnings: list[str] = []
        if outside:
            warnings.append(f"{outside} source(s) keep their files outside {LIBRARIES}/ and are not in this archive")
        if skipped:
            warnings.append(f"{skipped} file(s) with unsupported names were left out")

        limit = split_bytes if split_bytes and split_bytes > 0 else None
        stored = {DB_ENTRY: deflated_size(db_file)} if limit else None  # what it takes in a zip
        plan = plan_parts([(db_file, DB_ENTRY, db_file.stat().st_size)] + sized, limit, stored)
        common = {
            "kind": KIND_LIBRARY,
            "created_at": started.isoformat(timespec="seconds"),
            "app_version": __version__,
            "include_thumbs": False,
            "user_id": user_id,
            "library_schema_version": facts["schema_version"],
            "assets": facts["assets"],
            "albums": facts["albums"],
        }
        paths, manifest = write_archive(out_dir, base, plan, common, warnings, on_progress)
        return LibraryBackupResult(paths[0], manifest, paths)
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --- check and stage ---------------------------------------------------------------------------


def _members(zf: zipfile.ZipFile, manifest: dict) -> list[zipfile.ZipInfo]:
    """The entries of one zip, checked one by one. Whether the database is all there is a question
    for the whole set (``_check_database``)."""
    infos = [i for i in zf.infolist() if not i.filename.endswith("/")]
    source = manifest["user_id"]
    seen: set[str] = set()
    for info in infos:
        name = info.filename
        safe_member(name)
        if name in seen:
            raise ArchiveError(f"duplicate name in archive: {name!r}")
        seen.add(name)
        if name in (MANIFEST, DB_ENTRY):
            continue
        _check_source(name, source)
    return infos


def _check_source(name: str, source: str) -> None:
    parts = name.split("/")
    if parts[0] != LIBRARIES or len(parts) < 3 or parts[1] != source:
        raise ArchiveError(f"unexpected name in a library archive: {name!r}")


def _check_database(names: set[str]) -> None:
    if DB_ENTRY not in names:
        raise ArchiveError("archive has no library.db")


def _check_manifest(manifest: dict) -> None:
    check_library_manifest(manifest, LIBRARY_CURRENT_VERSION)


def check_library_archive(archive: Path, *, full: bool = False, part_only: bool = False) -> dict:
    """Validate a library archive and return its manifest. ``full`` also reads every byte (CRC).

    A split backup is checked whole: every part must sit beside ``archive``. ``part_only`` checks just
    this file (an upload, before the other parts have arrived) and returns its own manifest.
    """
    try:
        loose: list[LooseFile] = []
        if part_only:
            own = read_manifest_file(archive)
            _check_manifest(own)
            paths, manifests, manifest = [archive], [own], own
        else:
            found = open_set(archive, _check_manifest)
            paths, manifests, manifest, loose = found.paths, found.manifests, found.manifest, found.loose
        names: set[str] = set()
        for path, part_manifest in zip(paths, manifests):
            with zipfile.ZipFile(path) as zf:
                infos = _members(zf, part_manifest)
                media = big_media(part_manifest)
                for info in infos:
                    if info.filename == MANIFEST:
                        continue
                    media += info.filename != DB_ENTRY
                    if info.filename in names:
                        raise ArchiveError(f"{info.filename!r} is in two parts of the backup")
                    names.add(info.filename)
                if media != part_manifest["file_count"]:
                    raise ArchiveError(
                        f"{path.name} lists {part_manifest['file_count']} files but holds {media}"
                    )
                if full:
                    bad = zf.testzip()
                    if bad is not None:
                        raise ArchiveError(f"archive is damaged: {bad!r} fails its checksum")
        for big in loose:
            name = big.entry.name
            if name in names:
                raise ArchiveError(f"{name!r} is in two parts of the backup")
            if name != DB_ENTRY:
                _check_source(name, manifest["user_id"])
            names.add(name)
            if full and hash_loose(big) != big.entry.sha256:
                raise ArchiveError(f"{big.path.name} is damaged: it does not match the backup")
        if not part_only:
            _check_database(names)
    except zipfile.BadZipFile as exc:
        raise ArchiveError(f"not a zip file or damaged: {exc}") from exc
    return manifest


def _stage_dirs(settings: Settings, stamp: str, user_id: str) -> tuple[Path, Path]:
    return (
        settings.database_path.parent / f"{USER_STAGE_PREFIX}{stamp}-{user_id}",
        settings.data_dir / f"{USER_DATA_STAGE_PREFIX}{stamp}-{user_id}",
    )


def stage_library(
    archive: Path, settings: Settings, user_id: str, *, on_progress: Progress | None = None
) -> StagedLibrary:
    """Unpack and check ``archive`` for ``user_id``. Nothing live changes; failure or cancel cleans up."""
    if not USER_ID.match(user_id or ""):
        raise ArchiveError(f"not a valid user id: {user_id!r}")
    ensure_data_dir_exists(settings.data_dir)
    stamp = _stamp()
    db_stage, data_stage = _stage_dirs(settings, stamp, user_id)
    try:
        found: ArchiveSet = open_set(archive, _check_manifest)
        manifest = found.manifest
        source = manifest["user_id"]
        parts = [(path, m) for path, m in zip(found.paths, found.manifests)]
        # First look: names, and how much room the unpacked library needs.
        db_size = 0
        media_size = 0
        total = len(found.loose)
        seen: set[str] = set()
        for path, part_manifest in parts:
            with zipfile.ZipFile(path) as zf:
                infos = _members(zf, part_manifest)
            total += len(infos)
            seen.update(i.filename for i in infos if i.filename != MANIFEST)
            db_size += sum(i.file_size for i in infos if i.filename == DB_ENTRY)
            media_size += sum(i.file_size for i in infos if i.filename not in (MANIFEST, DB_ENTRY))
        for big in found.loose:
            name = big.entry.name
            if name in seen:
                raise ArchiveError(f"{name!r} is in two parts of the backup")
            if name != DB_ENTRY:
                _check_source(name, source)
            seen.add(name)
            if name == DB_ENTRY:
                db_size += big.entry.size
            else:
                media_size += big.entry.size
        _check_database(seen)
        _require_space(settings.database_path.parent, db_size * 2)
        _require_space(settings.data_dir, media_size)
        db_stage.mkdir(parents=True)
        target_root = data_stage / LIBRARIES / user_id
        target_root.mkdir(parents=True)
        index = 0
        media = 0
        for path, part_manifest in parts:
            with zipfile.ZipFile(path) as zf:
                for info in _members(zf, part_manifest):
                    index += 1
                    name = info.filename
                    if name == MANIFEST:
                        continue
                    if name == DB_ENTRY:
                        dest = db_stage / DB_ENTRY
                    else:
                        dest = target_root / name.split("/", 2)[2]
                        media += 1
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        with zf.open(info) as src, open(dest, "wb") as out:
                            shutil.copyfileobj(src, out, 1024 * 1024)
                    except zipfile.BadZipFile as exc:
                        raise ArchiveError(f"archive is damaged at {name!r}: {exc}") from exc
                    if name != DB_ENTRY:
                        try:
                            stamp_time = datetime(*info.date_time, tzinfo=timezone.utc).timestamp()
                            os.utime(dest, (stamp_time, stamp_time))
                        except (ValueError, OSError):
                            pass
                    if on_progress is not None:
                        on_progress(index, total, name)
        for big in found.loose:
            index += 1
            name = big.entry.name
            if name == DB_ENTRY:
                copy_loose(big, db_stage / DB_ENTRY)
            else:
                copy_loose(big, target_root / name.split("/", 2)[2])
                media += 1
            if on_progress is not None:
                on_progress(index, total, name)
        if media != manifest["file_count"]:
            raise ArchiveError(f"archive lists {manifest['file_count']} files but holds {media}")
        upgrade_from = _prepare_library(
            db_stage / DB_ENTRY,
            settings.data_dir,
            source,
            manifest,
            int(datetime.now(timezone.utc).timestamp()),
            target_user_id=user_id,
            listed={"assets": manifest["assets"], "albums": manifest["albums"]},
        )
        return StagedLibrary(manifest, stamp, user_id, db_stage, data_stage, upgrade_from)
    except zipfile.BadZipFile as exc:
        _remove(db_stage)
        _remove(data_stage)
        raise ArchiveError(f"not a zip file or damaged: {exc}") from exc
    except BaseException:
        _remove(db_stage)
        _remove(data_stage)
        raise


# --- swap --------------------------------------------------------------------------------------


def _journal(settings: Settings, user_id: str) -> Path:
    return settings.database_path.parent / f"{JOURNAL_PREFIX}{user_id}.json"


def _rename(src: Path, dest: Path, *, wait: float) -> None:
    """Rename, retrying while something still has the file open (Windows)."""
    deadline = time.monotonic() + wait
    while True:
        try:
            src.rename(dest)
            return
        except PermissionError as exc:
            if time.monotonic() >= deadline:
                raise ArchiveError(
                    "your library is in use by another request; nothing was changed, try again in a moment"
                ) from exc
            time.sleep(0.25)


def swap_library(
    staged: StagedLibrary,
    settings: Settings,
    libraries: LibraryDbs,
    *,
    wait: float = SWAP_WAIT_SECONDS,
) -> None:
    """Replace the user's live library with the staged one. Requests for this user get ``503`` meanwhile.

    The previous library is deleted once the new one is in place (not before: a failed swap rolls back).
    """
    user_id = staged.user_id
    live_db_dir = settings.accounts_dir / user_id
    live_files = settings.data_dir / LIBRARIES / user_id
    aside_db = settings.database_path.parent / ASIDE / f"{staged.stamp}-{user_id}-db"
    aside_files = settings.data_dir / ASIDE / f"{staged.stamp}-{user_id}-files"
    journal = _journal(settings, user_id)

    moved: list[tuple[Path, Path]] = []  # (original place, where it went) for the rollback
    placed: list[Path] = []
    try:
        with libraries.locked(user_id):
            _checkpoint_library_db(live_db_dir / DB_ENTRY)
            offset_revs(staged.db_stage / DB_ENTRY, live_max_rev(live_db_dir / DB_ENTRY))
            journal.write_text(
                json.dumps(
                    {
                        "user_id": user_id,
                        "stamp": staged.stamp,
                        "aside_db": str(aside_db),
                        "aside_files": str(aside_files),
                    }
                ),
                encoding="utf-8",
            )
            for live, aside in ((live_db_dir, aside_db), (live_files, aside_files)):
                if live.exists():
                    aside.parent.mkdir(parents=True, exist_ok=True)
                    _rename(live, aside, wait=wait)
                    moved.append((live, aside))
            live_db_dir.parent.mkdir(parents=True, exist_ok=True)
            staged.db_stage.rename(live_db_dir)
            placed.append(live_db_dir)
            new_files = staged.data_stage / LIBRARIES / user_id
            live_files.parent.mkdir(parents=True, exist_ok=True)
            new_files.rename(live_files)
            placed.append(live_files)
    except BaseException:
        for path in reversed(placed):
            _remove(path)
        for original, aside in reversed(moved):
            if aside.exists() and not original.exists():
                aside.rename(original)
        journal.unlink(missing_ok=True)
        raise
    finally:
        _remove(staged.db_stage)
        _remove(staged.data_stage)
    journal.unlink(missing_ok=True)
    # Deleted outside the lock, so a big library does not keep this user's requests waiting. Anything a
    # crash leaves here is removed at the next start (remove_stale_user_staging).
    for _, aside in moved:
        _remove(aside)


def recover_library_swaps(settings: Settings) -> list[str]:
    """Start-up: finish or undo a swap that a crash cut short. Returns the user ids it put back.

    The journal exists only between "start moving the live library aside" and "the new one is in
    place". If it is still there the new library may be missing or partial, so put the old one back.
    """
    recovered: list[str] = []
    folder = settings.database_path.parent
    for journal in sorted(folder.glob(f"{JOURNAL_PREFIX}*.json")):
        try:
            data = json.loads(journal.read_text(encoding="utf-8"))
            user_id = str(data["user_id"])
            if not USER_ID.match(user_id):
                raise ValueError("bad user id")
            for live, aside in (
                (settings.accounts_dir / user_id, Path(data["aside_db"])),
                (settings.data_dir / LIBRARIES / user_id, Path(data["aside_files"])),
            ):
                if aside.exists():
                    if live.exists():
                        _remove(live)
                    live.parent.mkdir(parents=True, exist_ok=True)
                    aside.rename(live)
            recovered.append(user_id)
        except (OSError, ValueError, KeyError):
            continue
        journal.unlink(missing_ok=True)
    return recovered


def remove_stale_user_staging(settings: Settings) -> None:
    """Start-up: remove unpacked restores, and old libraries awaiting deletion, that a stop left behind.

    Run after ``recover_library_swaps``, which may still need an old library to put back.
    """
    for root, prefix in (
        (settings.database_path.parent, USER_STAGE_PREFIX),
        (settings.data_dir, USER_DATA_STAGE_PREFIX),
    ):
        if root.is_dir():
            for entry in root.glob(f"{prefix}*"):
                _remove(entry)
    for root in (settings.database_path.parent, settings.data_dir):
        _remove(root / ASIDE)


__all__ = [
    "LibraryBackupResult",
    "StagedLibrary",
    "backup_library",
    "check_library_archive",
    "library_archive_name",
    "recover_library_swaps",
    "remove_stale_user_staging",
    "stage_library",
    "swap_library",
    "user_backup_dir",
]
