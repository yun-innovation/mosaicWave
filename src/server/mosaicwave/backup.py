"""Site backup: one zip with the site database, every user's library database and originals,
and (optionally) thumbs.

Safe while the API runs: databases are copied with SQLite's backup API; files are read as they
are. Command line: ``python -m mosaicwave.backup [--include-thumbs] [--out DIR]``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from mosaicwave import __version__
from mosaicwave.archive import (
    BLOBS,
    DATA_PREFIX,
    DB_ENTRY,
    KIND_SITE,
    LIBRARIES,
    ArchiveError,
    account_entry,
    iter_files,
    deflated_size,
    plan_parts,
    safe_member,
    write_archive,
)
from mosaicwave.config import Settings
from mosaicwave.db import _connect_sqlite
from mosaicwave.library.dbs import LibraryDbs
from mosaicwave.schema.library import LIBRARY_CURRENT_VERSION

Progress = Callable[[int, int, str], None]


@dataclass(frozen=True)
class BackupResult:
    path: Path  # the archive, or the first part of a split backup
    manifest: dict  # for the whole backup
    paths: list[Path] = field(default_factory=list)


def archive_name(now: datetime | None = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    return f"mosaicwave-site-{stamp}.zip"


def _copy_db(source: Path, dest: Path) -> sqlite3.Connection:
    """Consistent copy of ``source`` (SQLite backup API); returns a connection to the copy."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = _connect_sqlite(source)
    out = sqlite3.connect(os.fspath(dest))
    try:
        src.backup(out)
    except BaseException:
        out.close()
        raise
    finally:
        src.close()
    out.execute("PRAGMA journal_mode=DELETE")
    return out


def _snapshot_site(settings: Settings, work: Path) -> tuple[Path, int, int]:
    """Copy the site database without the running jobs. Returns (file, schema version, users)."""
    source = settings.database_path
    if not source.is_file():
        raise ArchiveError("library.db does not exist yet; nothing to back up")
    dest = work / DB_ENTRY
    con = _copy_db(source, dest)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        version = int(con.execute("PRAGMA user_version").fetchone()[0])
        if "job" in tables:
            # The backup job itself is running in the snapshot; a restore should not show it.
            con.execute(
                "DELETE FROM job_event WHERE job_id IN (SELECT id FROM job WHERE status='running')"
            )
            con.execute("DELETE FROM job WHERE status='running'")
        users = 0
        if "user" in tables:
            users = int(con.execute('SELECT COUNT(*) FROM "user" WHERE deleted_at IS NULL').fetchone()[0])
        con.commit()
    finally:
        con.close()
    return dest, version, users


def _snapshot_library(
    settings: Settings, user_id: str, work: Path, *, dest: Path | None = None
) -> tuple[Path, dict, int]:
    """Copy one user's library database and make its source paths portable.

    Returns (file, facts, sources kept outside libraries/).
    """
    source = settings.accounts_dir / user_id / DB_ENTRY
    dest = dest or work / account_entry(user_id)
    libraries = (settings.data_dir / LIBRARIES).resolve()
    con = _copy_db(source, dest)
    try:
        version = int(con.execute("PRAGMA user_version").fetchone()[0])
        outside = 0
        for source_id, uri in list(con.execute("SELECT id, root_uri FROM source")):
            try:
                rel = Path(uri).resolve().relative_to(libraries)
            except (ValueError, OSError):
                outside += 1
                continue
            portable = f"{DATA_PREFIX}{LIBRARIES}/{rel.as_posix()}"
            con.execute("UPDATE source SET root_uri = ? WHERE id = ?", (portable, source_id))
        assets = int(con.execute("SELECT COUNT(*) FROM asset WHERE deleted_at IS NULL").fetchone()[0])
        albums = int(con.execute("SELECT COUNT(*) FROM album WHERE deleted_at IS NULL").fetchone()[0])
        con.commit()
    finally:
        con.close()
    return dest, {"schema_version": version, "assets": assets, "albums": albums}, outside


def backup_site(
    settings: Settings,
    out_dir: Path,
    *,
    include_thumbs: bool = False,
    split_bytes: int | None = None,
    on_progress: Progress | None = None,
) -> BackupResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).replace(microsecond=0)  # one clock for the name and the manifest
    base = archive_name(started)[: -len(".zip")]
    work = Path(tempfile.mkdtemp(prefix="mosaicwave-backup-"))
    try:
        warnings: list[str] = []
        site_db, site_version, users = _snapshot_site(settings, work)

        user_ids = LibraryDbs(settings).user_ids()
        libraries: dict[str, dict] = {}
        db_files: list[tuple[Path, str]] = [(site_db, DB_ENTRY)]
        outside_total = 0
        for user_id in user_ids:
            lib_db, facts, outside = _snapshot_library(settings, user_id, work)
            libraries[user_id] = facts
            outside_total += outside
            db_files.append((lib_db, account_entry(user_id)))
        if outside_total:
            warnings.append(
                f"{outside_total} source(s) keep their files outside {LIBRARIES}/ and are not in this archive"
            )

        files: list[tuple[Path, str]] = []
        skipped_names = 0
        roots = [(settings.data_dir / LIBRARIES, LIBRARIES)]
        if include_thumbs:
            roots.append((settings.blob_dir, BLOBS))
        for root, prefix in roots:
            for path, arcname in iter_files(root, prefix):
                try:
                    safe_member(arcname)
                except ArchiveError:
                    skipped_names += 1
                    continue
                files.append((path, arcname))
        if skipped_names:
            warnings.append(f"{skipped_names} file(s) with unsupported names were left out")

        sized: list[tuple[Path, str, int]] = []
        for path, arcname in files:
            try:
                sized.append((path, arcname, path.stat().st_size))
            except OSError:
                sized.append((path, arcname, 0))
        everything = [(f, arcname, f.stat().st_size) for f, arcname in db_files] + sized
        limit = split_bytes if split_bytes and split_bytes > 0 else None
        # What a database takes in a zip is its deflated size; that is what counts against the part size.
        stored = {arcname: deflated_size(f) for f, arcname in db_files} if limit else None
        plan = plan_parts(everything, limit, stored)
        common = {
            "kind": KIND_SITE,
            "created_at": started.isoformat(timespec="seconds"),
            "app_version": __version__,
            "include_thumbs": include_thumbs,
            "schema_version": site_version,
            "library_schema_version": max(
                [f["schema_version"] for f in libraries.values()] or [LIBRARY_CURRENT_VERSION]
            ),
            "user_ids": user_ids,
            "libraries": libraries,
            "users": users,
            "assets": sum(f["assets"] for f in libraries.values()),
            "albums": sum(f["albums"] for f in libraries.values()),
        }
        paths, manifest = write_archive(out_dir, base, plan, common, warnings, on_progress)
        return BackupResult(paths[0], manifest, paths)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Back up this app's whole library (site database, every user's library database and "
            "originals) into one zip. Safe while the API runs. Uses the same data folder the API uses."
        )
    )
    parser.add_argument("--out", type=Path, help="Folder for the archive (default: {app folder}/backups)")
    parser.add_argument(
        "--include-thumbs", action="store_true", help="Also store thumbnails (regenerable; can be large)"
    )
    parser.add_argument(
        "--split-gb",
        type=float,
        help="Split into zips of about this many GB (default: one zip). Keep all the parts together",
    )
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    out_dir = args.out if args.out else settings.backup_dir

    def show(done: int, total: int, _name: str) -> None:
        if total and (done == total or done % 200 == 0):
            print(f"\r{done}/{total} files", end="", file=sys.stderr, flush=True)

    try:
        split = int(args.split_gb * 2**30) if args.split_gb and args.split_gb > 0 else None
        result = backup_site(
            settings, out_dir, include_thumbs=args.include_thumbs, split_bytes=split, on_progress=show
        )
    except (ArchiveError, OSError, sqlite3.Error) as exc:
        print(f"\nbackup failed: {exc}", file=sys.stderr)
        return 1
    print(file=sys.stderr)
    m = result.manifest
    for path in result.paths:
        print(path)
    print(f"{m['assets']} photos/videos, {m['albums']} albums, {m['users']} users, {m['file_count']} files")
    for warning in m["warnings"]:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
