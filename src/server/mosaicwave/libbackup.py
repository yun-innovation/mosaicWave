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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from mosaicwave import __version__
from mosaicwave.archive import (
    DB_ENTRY,
    FORMAT,
    KIND_LIBRARY,
    LIBRARIES,
    MANIFEST,
    ArchiveError,
    check_library_manifest,
    iter_files,
    read_manifest,
    safe_member,
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
    path: Path
    manifest: dict


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
    settings: Settings, user_id: str, out_dir: Path, *, on_progress: Progress | None = None
) -> LibraryBackupResult:
    if not USER_ID.match(user_id or ""):
        raise ArchiveError(f"not a valid user id: {user_id!r}")
    if not (settings.accounts_dir / user_id / DB_ENTRY).is_file():
        raise ArchiveError("this library has nothing to back up yet")
    out_dir.mkdir(parents=True, exist_ok=True)
    final = out_dir / library_archive_name()
    part = final.with_name(final.name + ".part")
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
        need = 0
        for path, _ in files:
            try:
                need += path.stat().st_size
            except OSError:
                continue
        _require_space(out_dir, need)

        db_file, facts, outside = _snapshot_library(settings, user_id, work, dest=work / DB_ENTRY)
        warnings: list[str] = []
        if outside:
            warnings.append(f"{outside} source(s) keep their files outside {LIBRARIES}/ and are not in this archive")
        if skipped:
            warnings.append(f"{skipped} file(s) with unsupported names were left out")

        total = len(files)
        written = 0
        total_bytes = 0
        vanished = 0
        part.unlink(missing_ok=True)
        with zipfile.ZipFile(part, "w", allowZip64=True, strict_timestamps=False) as zf:
            zf.write(db_file, DB_ENTRY, compress_type=zipfile.ZIP_DEFLATED)
            for index, (path, arcname) in enumerate(files, start=1):
                try:
                    zf.write(path, arcname, compress_type=zipfile.ZIP_STORED)
                    written += 1
                    total_bytes += zf.getinfo(arcname).file_size
                except FileNotFoundError:
                    vanished += 1
                if on_progress is not None:
                    on_progress(index, total, arcname)
            if vanished:
                warnings.append(f"{vanished} file(s) disappeared while the backup ran")
            manifest = {
                "format": FORMAT,
                "kind": KIND_LIBRARY,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "app_version": __version__,
                "include_thumbs": False,
                "user_id": user_id,
                "library_schema_version": facts["schema_version"],
                "assets": facts["assets"],
                "albums": facts["albums"],
                "file_count": written,
                "total_bytes": total_bytes,
                "warnings": warnings,
            }
            zf.writestr(MANIFEST, json.dumps(manifest, indent=2, sort_keys=True), zipfile.ZIP_DEFLATED)
        os.replace(part, final)
        return LibraryBackupResult(final, manifest)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --- check and stage ---------------------------------------------------------------------------


def _members(zf: zipfile.ZipFile, manifest: dict) -> list[zipfile.ZipInfo]:
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
        parts = name.split("/")
        if parts[0] != LIBRARIES or len(parts) < 3 or parts[1] != source:
            raise ArchiveError(f"unexpected name in a library archive: {name!r}")
    if DB_ENTRY not in seen:
        raise ArchiveError("archive has no library.db")
    return infos


def check_library_archive(archive: Path, *, full: bool = False) -> dict:
    """Validate a library archive and return its manifest. ``full`` also reads every byte (CRC)."""
    try:
        with zipfile.ZipFile(archive) as zf:
            manifest = read_manifest(zf)
            check_library_manifest(manifest, LIBRARY_CURRENT_VERSION)
            _members(zf, manifest)
            if full:
                bad = zf.testzip()
                if bad is not None:
                    raise ArchiveError(f"archive is damaged: {bad!r} fails its checksum")
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
        with zipfile.ZipFile(archive) as zf:
            manifest = read_manifest(zf)
            check_library_manifest(manifest, LIBRARY_CURRENT_VERSION)
            infos = _members(zf, manifest)
            _require_space(
                settings.database_path.parent,
                sum(i.file_size for i in infos if i.filename == DB_ENTRY) * 2,
            )
            _require_space(settings.data_dir, sum(i.file_size for i in infos if i.filename != DB_ENTRY))
            db_stage.mkdir(parents=True)
            source = manifest["user_id"]
            target_root = data_stage / LIBRARIES / user_id
            target_root.mkdir(parents=True)
            total = len(infos)
            media = 0
            for index, info in enumerate(infos, start=1):
                name = info.filename
                if name == MANIFEST:
                    continue
                if name == DB_ENTRY:
                    dest = db_stage / DB_ENTRY
                else:
                    rel = name.split("/", 2)[2]
                    dest = target_root / rel
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
