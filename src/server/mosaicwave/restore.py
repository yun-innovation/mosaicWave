"""Restore a site backup (doc/backup.md). Offline: stop the API first.

``python -m mosaicwave.restore ARCHIVE [--check]``

Everything is validated and staged before anything is replaced. The databases (site file and
``accounts/``) live in the app folder and the photos (``libraries/``, ``blobs/``) in the data
folder, which can be another volume, so each side is staged next to where it will go and the
swap is a rename. The current data is kept as ``*.before-restore-{time}`` (offline restore) or deleted once the new data
is in place (online restore, ``keep_old=False``); a failure while
swapping puts it back. Databases older than this app are upgraded by the normal schema steps
the next time the API starts (with the usual ``library.db.pre-v{N}`` copy).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import sqlite3
import sys
import threading
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from mosaicwave import schema
from mosaicwave.archive import (
    ACCOUNTS,
    BLOBS,
    DATA_PREFIX,
    DB_ENTRY,
    LIBRARIES,
    MANIFEST,
    ArchiveError,
    account_entry,
    check_manifest,
    read_manifest,
    safe_member,
)
from mosaicwave.config import Settings, _checkpoint_library_db, ensure_data_dir_exists
from mosaicwave.schema.library import LIBRARY_CURRENT_VERSION

Progress = Callable[[int, int, str], None]

_COPY_CHUNK = 1024 * 1024
_NEEDS = {"source", "asset", "album", "album_asset"}

APP_STAGE_PREFIX = ".restore-staging-app-"
DATA_STAGE_PREFIX = ".restore-staging-data-"
DISCARD_MARK = ".discard-"
PENDING_NAME = "restore-pending.json"
RESULT_NAME = "restore-result.json"
_STAMP = re.compile(r"^\d{8}-\d{6}$")


@dataclass
class StagedRestore:
    """An archive unpacked and checked, waiting to be swapped in."""

    manifest: dict
    stamp: str
    app_stage: Path
    data_stage: Path
    site_upgrade_from: int | None = None
    library_upgrade_from: int | None = None


@dataclass
class RestoreResult:
    manifest: dict
    site_upgrade_from: int | None = None
    library_upgrade_from: int | None = None
    kept: list[Path] = field(default_factory=list)
    discarded: list[Path] = field(default_factory=list)


def _members(zf: zipfile.ZipFile, manifest: dict) -> list[zipfile.ZipInfo]:
    infos = [i for i in zf.infolist() if not i.filename.endswith("/")]
    seen: set[str] = set()
    for info in infos:
        safe_member(info.filename)
        if info.filename in seen:
            raise ArchiveError(f"duplicate name in archive: {info.filename!r}")
        seen.add(info.filename)
    if DB_ENTRY not in seen:
        raise ArchiveError("archive has no library.db")
    wanted = {account_entry(user_id) for user_id in manifest["user_ids"]}
    found = {name for name in seen if name.startswith(f"{ACCOUNTS}/")}
    if wanted != found:
        raise ArchiveError("the library databases in the archive do not match its manifest")
    return infos


def check_archive(archive: Path, *, full: bool = False) -> dict:
    """Validate ``archive`` and return its manifest. ``full`` also reads every byte (CRC)."""
    try:
        with zipfile.ZipFile(archive) as zf:
            manifest = read_manifest(zf)
            check_manifest(manifest, schema.CURRENT_VERSION, LIBRARY_CURRENT_VERSION)
            _members(zf, manifest)
            if full:
                bad = zf.testzip()
                if bad is not None:
                    raise ArchiveError(f"archive is damaged: {bad!r} fails its checksum")
    except zipfile.BadZipFile as exc:
        raise ArchiveError(f"not a zip file or damaged: {exc}") from exc
    return manifest


def _require_unlocked(db: Path) -> None:
    if not db.is_file():
        return
    try:
        con = sqlite3.connect(os.fspath(db), timeout=1.0)
        try:
            con.execute("BEGIN EXCLUSIVE")
            con.execute("ROLLBACK")
        finally:
            con.close()
    except sqlite3.OperationalError as exc:
        raise ArchiveError("library.db is in use; stop the API and try again") from exc


def _is_media(name: str) -> bool:
    return name.startswith((f"{LIBRARIES}/", f"{BLOBS}/"))


def _extract(
    zf: zipfile.ZipFile,
    infos: list[zipfile.ZipInfo],
    app_stage: Path,
    data_stage: Path,
    on_progress: Progress | None,
) -> int:
    """Copy members into the stages (CRC is checked as each file is read). Returns media count."""
    total = len(infos)
    media = 0
    for index, info in enumerate(infos, start=1):
        name = info.filename
        if name == MANIFEST:
            continue
        target = (data_stage if _is_media(name) else app_stage) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, _COPY_CHUNK)
        except zipfile.BadZipFile as exc:
            raise ArchiveError(f"archive is damaged at {name!r}: {exc}") from exc
        if _is_media(name):
            media += 1
            try:
                stamp = datetime(*info.date_time, tzinfo=timezone.utc).timestamp()
                os.utime(target, (stamp, stamp))
            except (ValueError, OSError):
                pass
        if on_progress is not None:
            on_progress(index, total, name)
    return media


def _check_file(path: Path, *, label: str) -> sqlite3.Connection:
    con = sqlite3.connect(os.fspath(path))
    try:
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ArchiveError(f"the {label} in the archive is damaged")
        if not schema.library_tables(con):
            raise ArchiveError(f"the {label} in the archive has no tables")
    except BaseException:
        con.close()
        raise
    return con


def _prepare_site(staged: Path, manifest: dict) -> int | None:
    con = _check_file(staged, label="site database")
    try:
        found = schema.stored_version(con)
        if found != manifest["schema_version"]:
            raise ArchiveError(
                f"manifest says site schema v{manifest['schema_version']} but the database is v{found}"
            )
    finally:
        con.close()
    return found if found < schema.CURRENT_VERSION else None


def _prepare_library(
    staged: Path,
    data_dir: Path,
    user_id: str,
    manifest: dict,
    thumb_bump: int | None = None,
    *,
    target_user_id: str | None = None,
    listed: dict | None = None,
) -> int | None:
    """Verify one library database and point its sources at ``data_dir``.

    ``target_user_id`` restores the library into another account: the first folder under
    ``libraries/`` and every source's owner become that user. ``listed`` is the manifest's counts for
    this library (a site archive keeps them per user; a library archive at the top level).

    ``thumb_bump`` is added to every ``thumb_rev``. Thumbnail URLs carry the version and browsers keep
    them for an hour, so without a bump a restored library (which has no thumbnail files) would keep
    showing the old cached images under unchanged URLs. The server rebuilds a missing thumbnail on
    its first request.
    """
    con = _check_file(staged, label=f"library database of {user_id}")
    try:
        if not _NEEDS <= schema.library_tables(con):
            raise ArchiveError(f"the library database of {user_id} is missing tables")
        found = schema.stored_version(con)
        if found > LIBRARY_CURRENT_VERSION:
            raise ArchiveError(f"the library database of {user_id} is schema v{found}, newer than this app")
        for source_id, uri in list(con.execute("SELECT id, root_uri FROM source")):
            if isinstance(uri, str) and uri.startswith(DATA_PREFIX):
                rel = uri[len(DATA_PREFIX) :].split("/")
                if target_user_id and len(rel) > 1 and rel[0] == LIBRARIES and rel[1] == user_id:
                    rel[1] = target_user_id
                real = data_dir.joinpath(*rel).resolve()
                con.execute("UPDATE source SET root_uri = ? WHERE id = ?", (str(real), source_id))
        if target_user_id:
            con.execute("UPDATE source SET owner_user_id = ?", (target_user_id,))
        if thumb_bump:
            con.execute("UPDATE asset SET thumb_rev = thumb_rev + ?", (thumb_bump,))
        con.commit()
        con.execute("PRAGMA journal_mode=DELETE")
        if listed is None:
            listed = manifest["libraries"].get(user_id, {})
        for table, key in (("asset", "assets"), ("album", "albums")):
            count = int(con.execute(f"SELECT COUNT(*) FROM {table} WHERE deleted_at IS NULL").fetchone()[0])
            if count != listed.get(key):
                raise ArchiveError(
                    f"archive lists {listed.get(key)} {key} for {user_id} but its database has {count}"
                )
    finally:
        con.close()
    return found if found < LIBRARY_CURRENT_VERSION else None


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def _require_space(folder: Path, need: int) -> None:
    free = shutil.disk_usage(folder).free
    if free < need + need // 20:
        raise ArchiveError(
            f"not enough free space in {folder}: need about {need // 2**20} MB, have {free // 2**20} MB"
        )


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _stage_dirs(settings: Settings, stamp: str) -> tuple[Path, Path]:
    return (
        settings.database_path.parent / f"{APP_STAGE_PREFIX}{stamp}",
        settings.data_dir / f"{DATA_STAGE_PREFIX}{stamp}",
    )


def stage_site(
    archive: Path, settings: Settings, *, on_progress: Progress | None = None
) -> StagedRestore:
    """Validate ``archive`` and unpack it into staging folders beside where the data will go.

    Nothing live is touched, so this is safe while the API runs. Any failure, and a cancel raised
    from ``on_progress``, removes the staging folders again.
    """
    data_dir = settings.data_dir
    ensure_data_dir_exists(data_dir)
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = _stamp()
    app_stage, data_stage = _stage_dirs(settings, stamp)
    try:
        with zipfile.ZipFile(archive) as zf:
            manifest = read_manifest(zf)
            check_manifest(manifest, schema.CURRENT_VERSION, LIBRARY_CURRENT_VERSION)
            infos = _members(zf, manifest)
            _require_space(
                settings.database_path.parent, sum(i.file_size for i in infos if not _is_media(i.filename))
            )
            _require_space(data_dir, sum(i.file_size for i in infos if _is_media(i.filename)))
            app_stage.mkdir(parents=True)
            data_stage.mkdir(parents=True)
            media = _extract(zf, infos, app_stage, data_stage, on_progress)
        if media != manifest.get("file_count"):
            raise ArchiveError(f"archive lists {manifest.get('file_count')} files but holds {media}")
        site_upgrade_from = _prepare_site(app_stage / DB_ENTRY, manifest)
        older: list[int] = []
        # No thumbnail files in the archive: give every asset a new thumbnail version so browsers refetch.
        # With thumbnails the versions must keep matching the restored files.
        thumb_bump = None if manifest.get("include_thumbs") else int(datetime.now(timezone.utc).timestamp())
        for user_id in manifest["user_ids"]:
            was = _prepare_library(
                app_stage / account_entry(user_id), data_dir, user_id, manifest, thumb_bump
            )
            if was is not None:
                older.append(was)
        return StagedRestore(
            manifest=manifest,
            stamp=stamp,
            app_stage=app_stage,
            data_stage=data_stage,
            site_upgrade_from=site_upgrade_from,
            library_upgrade_from=min(older) if older else None,
        )
    except zipfile.BadZipFile as exc:
        _remove(app_stage)
        _remove(data_stage)
        raise ArchiveError(f"not a zip file or damaged: {exc}") from exc
    except BaseException:
        _remove(app_stage)
        _remove(data_stage)
        raise


def live_max_rev(db: Path) -> int:
    """The highest sync number in a library database (0 when there is none). Read only."""
    if not db.is_file():
        return 0
    try:
        con = sqlite3.connect(os.fspath(db.resolve()), timeout=5.0)
    except sqlite3.Error:
        return 0
    try:
        top = 0
        for table in ("asset", "album", "album_asset"):
            try:
                top = max(top, int(con.execute(f"SELECT COALESCE(MAX(rev), 0) FROM {table}").fetchone()[0]))
            except sqlite3.Error:
                continue
        return top
    finally:
        con.close()


def offset_revs(staged_db: Path, offset: int) -> None:
    """Move every restored row's sync number above ``offset``.

    A sync client remembers the number it last pulled. A restored library goes back in time, so rows
    that look old to a client would never be pulled again. Raising them above anything the live library
    ever handed out (``offset`` is its highest number) makes every client pull them afresh. Order is
    kept; ``updated_at`` and the album ``rev`` that guards edits are left alone except by this shift.
    """
    if offset <= 0:
        return
    con = sqlite3.connect(os.fspath(staged_db))
    try:
        for table in ("asset", "album", "album_asset"):
            con.execute(f"UPDATE {table} SET rev = rev + ?", (offset,))
        con.commit()
    finally:
        con.close()


def commit_site(staged: StagedRestore, settings: Settings, *, keep_old: bool = True) -> RestoreResult:
    """Swap the staged data in. The databases must not be open (offline, or at API start-up).

    ``keep_old`` keeps the replaced data as ``*.before-restore-{time}`` (the offline command: the last
    resort when the server will not start). Without it the replaced data is deleted once the new data
    is in place, in a background thread so a large library does not hold up the start.
    """
    for staged_db in sorted((staged.app_stage / ACCOUNTS).glob(f"*/{DB_ENTRY}")):
        offset_revs(staged_db, live_max_rev(settings.accounts_dir / staged_db.parent.name / DB_ENTRY))
    result = RestoreResult(
        manifest=staged.manifest,
        site_upgrade_from=staged.site_upgrade_from,
        library_upgrade_from=staged.library_upgrade_from,
    )
    try:
        _swap(staged.app_stage, staged.data_stage, settings, staged.stamp, result, keep_old=keep_old)
    finally:
        _remove(staged.app_stage)
        _remove(staged.data_stage)
    if result.discarded:
        threading.Thread(target=_remove_all, args=(result.discarded,), daemon=True).start()
    return result


def restore_site(archive: Path, settings: Settings, *, on_progress: Progress | None = None) -> RestoreResult:
    """Offline restore: stage and swap in one go."""
    ensure_data_dir_exists(settings.data_dir)
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    _require_unlocked(settings.database_path)
    return commit_site(stage_site(archive, settings, on_progress=on_progress), settings)


# --- Online restore: stage while the API runs, swap at the next start --------------------------


def pending_path(settings: Settings) -> Path:
    return settings.database_path.parent / PENDING_NAME


def result_path(settings: Settings) -> Path:
    return settings.database_path.parent / RESULT_NAME


def _summary(manifest: dict) -> dict:
    keys = ("created_at", "app_version", "schema_version", "include_thumbs", "users", "assets", "albums", "file_count")
    return {key: manifest.get(key) for key in keys}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_pending(settings: Settings, staged: StagedRestore, archive_name: str) -> None:
    """Record a staged restore. It is applied the next time the API starts."""
    _write_json(
        pending_path(settings),
        {
            "archive": archive_name,
            "stamp": staged.stamp,
            "staged_at": datetime.now(timezone.utc).isoformat(),
            "summary": _summary(staged.manifest),
            "site_upgrade_from": staged.site_upgrade_from,
            "library_upgrade_from": staged.library_upgrade_from,
        },
    )


def pending_exists(settings: Settings) -> bool:
    return pending_path(settings).exists()


def read_pending(settings: Settings) -> dict | None:
    """The staged restore, or None. A damaged record counts as none (it is cleaned at start)."""
    data = _read_json(pending_path(settings))
    if data is None or not _STAMP.match(str(data.get("stamp", ""))):
        return None
    return data


def discard_pending(settings: Settings) -> bool:
    """Throw a staged restore away. True when there was one."""
    existed = pending_path(settings).exists()
    pending = read_pending(settings)
    if pending is not None:
        for stage in _stage_dirs(settings, str(pending["stamp"])):
            _remove(stage)
    pending_path(settings).unlink(missing_ok=True)
    if existed:
        _remove_stale_stages(settings)
    return existed


def read_result(settings: Settings) -> dict | None:
    return _read_json(result_path(settings))


def clear_result(settings: Settings) -> bool:
    existed = result_path(settings).exists()
    result_path(settings).unlink(missing_ok=True)
    return existed


def _remove_stale_uploads(settings: Settings) -> None:
    """Half-received uploads left by a server that stopped mid-upload. Only safe at start-up."""
    if settings.backup_dir.is_dir():
        for entry in settings.backup_dir.glob(".upload-*"):
            _remove(entry)


def _remove_all(paths: list[Path]) -> None:
    for path in paths:
        _remove(path)


def _remove_stale_stages(settings: Settings) -> None:
    """Staging folders, and replaced data a stopped server had not finished deleting."""
    for folder, prefix in (
        (settings.database_path.parent, APP_STAGE_PREFIX),
        (settings.data_dir, DATA_STAGE_PREFIX),
    ):
        if folder.is_dir():
            for entry in folder.glob(f"{prefix}*"):
                _remove(entry)
    for folder in {settings.database_path.parent, settings.data_dir}:
        if folder.is_dir():
            for entry in folder.glob(f"*{DISCARD_MARK}????????-??????"):
                _remove(entry)


def apply_pending(settings: Settings) -> str | None:
    """Start-up hook, before any database is opened. Returns ``done``, ``error`` or None.

    With no staged restore it only clears leftover staging folders. Otherwise it swaps the staged data
    in and leaves a result record for the admin; a failure puts the old data back and is recorded too.
    """
    _remove_stale_uploads(settings)
    record = _read_json(pending_path(settings))
    if record is None and not pending_path(settings).exists():
        _remove_stale_stages(settings)
        return None
    outcome: dict = {
        "archive": str((record or {}).get("archive", "")),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "summary": (record or {}).get("summary") or {},
    }
    try:
        stamp = str((record or {}).get("stamp", ""))
        if not _STAMP.match(stamp):
            raise ArchiveError("the staged restore record is damaged")
        app_stage, data_stage = _stage_dirs(settings, stamp)
        if not (app_stage / DB_ENTRY).is_file() or not data_stage.is_dir():
            raise ArchiveError("the staged restore files are missing")
        manifest_stub = {"stamp": stamp}
        staged = StagedRestore(
            manifest=manifest_stub,
            stamp=stamp,
            app_stage=app_stage,
            data_stage=data_stage,
            site_upgrade_from=(record or {}).get("site_upgrade_from"),
            library_upgrade_from=(record or {}).get("library_upgrade_from"),
        )
        result = commit_site(staged, settings, keep_old=False)
        outcome.update(
            status="done",
            kept=[str(path) for path in result.kept],
            upgrade=result.site_upgrade_from is not None or result.library_upgrade_from is not None,
            thumbs_missing=not (outcome["summary"] or {}).get("include_thumbs", False),
            message=None,
        )
        status = "done"
    except (ArchiveError, OSError, sqlite3.Error) as exc:
        outcome.update(status="error", kept=[], upgrade=False, thumbs_missing=False, message=str(exc))
        status = "error"
    _write_json(result_path(settings), outcome)
    pending_path(settings).unlink(missing_ok=True)
    _remove_stale_stages(settings)
    return status


def _swap(
    app_stage: Path,
    data_stage: Path,
    settings: Settings,
    stamp: str,
    result: RestoreResult,
    *,
    keep_old: bool = True,
) -> None:
    data_dir = settings.data_dir
    db_path = settings.database_path
    accounts = settings.accounts_dir
    _checkpoint_library_db(db_path)

    aside: list[tuple[Path, Path]] = []
    placed: list[Path] = []
    try:
        for current in (db_path, accounts, data_dir / LIBRARIES, data_dir / BLOBS):
            if current.exists():
                mark = ".before-restore-" if keep_old else DISCARD_MARK
                kept = current.with_name(f"{current.name}{mark}{stamp}")
                current.rename(kept)
                aside.append((current, kept))
        for sidecar in ("-wal", "-shm", "-journal"):
            Path(str(db_path) + sidecar).unlink(missing_ok=True)

        for stage, name, new in (
            (data_stage, LIBRARIES, data_dir / LIBRARIES),
            (data_stage, BLOBS, data_dir / BLOBS),
            (app_stage, ACCOUNTS, accounts),
        ):
            staged = stage / name
            if staged.is_dir():
                staged.rename(new)
            else:
                new.mkdir()
            placed.append(new)
        shutil.move(os.fspath(app_stage / DB_ENTRY), os.fspath(db_path))
        placed.append(db_path)
    except BaseException:
        for path in reversed(placed):
            _remove(path)
        for original, kept in reversed(aside):
            if kept.exists():
                kept.rename(original)
        raise
    if keep_old:
        result.kept = [kept for _, kept in aside]
    else:
        result.discarded = [kept for _, kept in aside]


def _api_listening(settings: Settings) -> bool:
    host = settings.bind_host if settings.bind_host not in ("0.0.0.0", "") else "127.0.0.1"
    try:
        with socket.create_connection((host, settings.bind_port), timeout=0.5):
            return True
    except OSError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Restore a mosaicWave site backup into this app's data folder (the one the API uses). "
            "Stop the API first. The current data is kept as *.before-restore-{time}."
        )
    )
    parser.add_argument("archive", type=Path, help="mosaicwave-site-*.zip")
    parser.add_argument(
        "--check", action="store_true", help="Only validate the archive (reads every byte); change nothing"
    )
    parser.add_argument("--force", action="store_true", help="Skip the check that the API port is free")
    args = parser.parse_args(argv)

    if not args.archive.is_file():
        print(f"no such file: {args.archive}", file=sys.stderr)
        return 1
    settings = Settings.from_env()
    try:
        if args.check:
            manifest = check_archive(args.archive, full=True)
            print(
                f"ok: {manifest['assets']} photos/videos, {manifest['albums']} albums, "
                f"{manifest['users']} users, {manifest['file_count']} files, "
                f"schema v{manifest['schema_version']} / library v{manifest['library_schema_version']}"
            )
            return 0
        if not args.force and _api_listening(settings):
            print(
                f"something is listening on {settings.bind_host}:{settings.bind_port}; stop the API "
                "first (or pass --force if that is not mosaicWave)",
                file=sys.stderr,
            )
            return 1

        def show(done: int, total: int, _name: str) -> None:
            if total and (done == total or done % 200 == 0):
                print(f"\r{done}/{total} files", end="", file=sys.stderr, flush=True)

        result = restore_site(args.archive, settings, on_progress=show)
    except (ArchiveError, OSError, sqlite3.Error) as exc:
        print(f"\nrestore failed, nothing was changed: {exc}", file=sys.stderr)
        return 1
    print(file=sys.stderr)
    m = result.manifest
    print(f"restored {m['assets']} photos/videos, {m['albums']} albums, {m['users']} users")
    if result.site_upgrade_from is not None or result.library_upgrade_from is not None:
        print("the archive is from an older mosaicWave: its databases are upgraded when the API starts")
    for path in result.kept:
        print(f"previous data kept: {path}")
    if not m.get("include_thumbs"):
        print("thumbnails are not in the archive: start the API and use Generate thumbs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
