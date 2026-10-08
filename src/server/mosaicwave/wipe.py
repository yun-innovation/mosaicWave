"""Wipe library.db and/or FileStore/BlobStore for the app's own configured data dir.

Always targets the same folder the API itself uses (Settings.from_env(), including
MOSAICWAVE_DATA_DIR if set). There is no --data-dir override: pointing this tool at an
arbitrary folder is how a wipe tool ends up destroying the wrong (or the live) library
by mistake. To operate on a different library, run with MOSAICWAVE_DATA_DIR set instead —
that is the one override the API itself honors, so this tool cannot silently diverge
from what the running app would use.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

from mosaicwave.config import Settings
from mosaicwave.db import make_engine

# Same payload names as config._FILE_PAYLOAD_NAMES: FileStore originals + BlobStore thumbs.
# Not {data_dir}/tmp (Takeout push staging) — that is not "storage" and is left alone.
STORAGE_DIR_NAMES = ("libraries", "blobs")

EXPECTED_TABLES = frozenset(
    {
        "user",
        "source",
        "source_grant",
        "local_credential",
        "job",
        "job_event",
        "asset",
        "album",
        "album_asset",
    }
)


def _sidecars(db: Path) -> list[Path]:
    return [db, Path(str(db) + "-wal"), Path(str(db) + "-shm")]


def table_names(db: Path) -> set[str]:
    con = sqlite3.connect(db)
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        return {row[0] for row in rows}
    finally:
        con.close()


def reset_library_db(db: Path) -> None:
    for path in _sidecars(db):
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise OSError(
                f"cannot remove {path.name}; stop the API (scripts\\stop-api.cmd) and retry ({exc})"
            ) from exc


def wipe_storage(data_dir: Path) -> None:
    """Delete {data_dir}/libraries and {data_dir}/blobs (originals + thumbs). Not library.db."""
    for name in STORAGE_DIR_NAMES:
        target = data_dir / name
        try:
            shutil.rmtree(target, ignore_errors=False)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise OSError(
                f"cannot remove {target}; stop the API (scripts\\stop-api.cmd) and retry ({exc})"
            ) from exc


def init_library_db(settings: Settings, *, reset: bool = False, wipe: bool = False) -> Path:
    path = settings.database_path
    if reset:
        reset_library_db(path)
    if wipe:
        wipe_storage(settings.data_dir)
    engine = make_engine(settings)
    engine.dispose()
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Wipe mosaicWave library.db and/or storage for this app's own data folder "
            "(same one the API uses), then recreate an empty schema. No flags: wipes "
            "both --db and --storage. Does not create users (that is POST /auth/setup)."
        )
    )
    parser.add_argument(
        "--db",
        action="store_true",
        help="Delete library.db (and WAL). With --storage omitted, photos/thumbs stay.",
    )
    parser.add_argument(
        "--storage",
        action="store_true",
        help=(
            "Delete {data_dir}/libraries (originals) and {data_dir}/blobs (thumbs). "
            "Does not touch {data_dir}/tmp (Takeout upload staging)."
        ),
    )
    args = parser.parse_args(argv)
    # No flags at all = wipe both (that is the point of this tool). Passing one flag
    # alone wipes only that one.
    if not args.db and not args.storage:
        args.db = True
        args.storage = True
    settings = Settings.from_env()
    try:
        path = init_library_db(settings, reset=args.db, wipe=args.storage)
    except OSError as exc:
        print(exc, file=sys.stderr)
        return 1
    missing = EXPECTED_TABLES - table_names(path)
    if missing:
        print(f"missing tables: {', '.join(sorted(missing))}", file=sys.stderr)
        return 1
    print(path)
    print("tables:", ", ".join(sorted(EXPECTED_TABLES)))
    if args.storage:
        print("storage wiped:", ", ".join(f"{settings.data_dir / n}" for n in STORAGE_DIR_NAMES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
