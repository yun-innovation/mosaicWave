"""Restore a site backup (doc/backup.md). Offline: stop the API first.

``python -m mosaicwave.restore ARCHIVE [--check]``

Everything is validated and staged before anything is replaced. The databases (site file and
``accounts/``) live in the app folder and the photos (``libraries/``, ``blobs/``) in the data
folder, which can be another volume, so each side is staged next to where it will go and the
swap is a rename. The current data is kept as ``*.before-restore-{time}``; a failure while
swapping puts it back. Databases older than this app are upgraded by the normal schema steps
the next time the API starts (with the usual ``library.db.pre-v{N}`` copy).
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import sqlite3
import sys
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


@dataclass
class RestoreResult:
    manifest: dict
    site_upgrade_from: int | None = None
    library_upgrade_from: int | None = None
    kept: list[Path] = field(default_factory=list)


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


def _prepare_library(staged: Path, data_dir: Path, user_id: str, manifest: dict) -> int | None:
    """Verify one library database and point its sources at ``data_dir``."""
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
                real = data_dir.joinpath(*rel).resolve()
                con.execute("UPDATE source SET root_uri = ? WHERE id = ?", (str(real), source_id))
        con.commit()
        con.execute("PRAGMA journal_mode=DELETE")
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


def restore_site(archive: Path, settings: Settings, *, on_progress: Progress | None = None) -> RestoreResult:
    data_dir = settings.data_dir
    ensure_data_dir_exists(data_dir)
    db_path = settings.database_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _require_unlocked(db_path)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    app_stage = db_path.parent / f".restore-staging-app-{stamp}"
    data_stage = data_dir / f".restore-staging-data-{stamp}"
    try:
        with zipfile.ZipFile(archive) as zf:
            manifest = read_manifest(zf)
            check_manifest(manifest, schema.CURRENT_VERSION, LIBRARY_CURRENT_VERSION)
            infos = _members(zf, manifest)
            _require_space(db_path.parent, sum(i.file_size for i in infos if not _is_media(i.filename)))
            _require_space(data_dir, sum(i.file_size for i in infos if _is_media(i.filename)))
            app_stage.mkdir(parents=True)
            data_stage.mkdir(parents=True)
            media = _extract(zf, infos, app_stage, data_stage, on_progress)
        if media != manifest.get("file_count"):
            raise ArchiveError(f"archive lists {manifest.get('file_count')} files but holds {media}")
        result = RestoreResult(manifest=manifest)
        result.site_upgrade_from = _prepare_site(app_stage / DB_ENTRY, manifest)
        older: list[int] = []
        for user_id in manifest["user_ids"]:
            was = _prepare_library(app_stage / account_entry(user_id), data_dir, user_id, manifest)
            if was is not None:
                older.append(was)
        result.library_upgrade_from = min(older) if older else None
        _swap(app_stage, data_stage, settings, stamp, result)
        return result
    except zipfile.BadZipFile as exc:
        raise ArchiveError(f"not a zip file or damaged: {exc}") from exc
    finally:
        _remove(app_stage)
        _remove(data_stage)


def _swap(app_stage: Path, data_stage: Path, settings: Settings, stamp: str, result: RestoreResult) -> None:
    data_dir = settings.data_dir
    db_path = settings.database_path
    accounts = settings.accounts_dir
    _checkpoint_library_db(db_path)

    aside: list[tuple[Path, Path]] = []
    placed: list[Path] = []
    try:
        for current in (db_path, accounts, data_dir / LIBRARIES, data_dir / BLOBS):
            if current.exists():
                kept = current.with_name(f"{current.name}.before-restore-{stamp}")
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
    result.kept = [kept for _, kept in aside]


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
