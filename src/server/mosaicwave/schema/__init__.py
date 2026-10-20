"""Versioned library.db schema: read the version, back up, apply upgrade steps in order.

The number is SQLite's PRAGMA user_version. Each change also adds a schema_history row.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path

from mosaicwave import __version__
from mosaicwave.schema.steps import STEPS, Step, column_names

CURRENT_VERSION = STEPS[-1].version
HISTORY_TABLE = "schema_history"

_log = logging.getLogger(__name__)


class SchemaTooNew(RuntimeError):
    def __init__(self, db: Path, found: int, supported: int) -> None:
        self.found = found
        self.supported = supported
        super().__init__(
            f"{db.name} is schema v{found}; this mosaicWave ({__version__}) supports up to "
            f"v{supported}. Install a newer mosaicWave, or restore {backup_path(db, supported).name} "
            "if it exists."
        )


def backup_path(db: Path, version: int) -> Path:
    return db.with_name(f"{db.name}.pre-v{version}")


def stored_version(con: sqlite3.Connection) -> int:
    return int(con.execute("PRAGMA user_version").fetchone()[0])


def library_tables(con: sqlite3.Connection) -> set[str]:
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    return {row[0] for row in rows} - {HISTORY_TABLE}


def guess_version(con: sqlite3.Connection) -> int:
    """Best match for a database from before schema versions (user_version 0)."""
    tables = library_tables(con)
    if "user" in tables and "locale" in column_names(con, "user"):
        return 2
    if (
        {"source", "album", "job"} <= tables
        and "owner_user_id" in column_names(con, "source")
        and "source_id" in column_names(con, "album")
        and "user_id" in column_names(con, "job")
    ):
        return 1
    return 0


def _record(con: sqlite3.Connection, version: int, note: str) -> None:
    con.execute(
        f"CREATE TABLE IF NOT EXISTS {HISTORY_TABLE} ("
        "version INTEGER NOT NULL, applied_at TEXT NOT NULL, "
        "app_version TEXT NOT NULL, note TEXT NOT NULL)"
    )
    con.execute(
        f"INSERT INTO {HISTORY_TABLE} (version, applied_at, app_version, note) VALUES (?, ?, ?, ?)",
        (version, datetime.now(timezone.utc).isoformat(timespec="seconds"), __version__, note),
    )
    con.execute(f"PRAGMA user_version = {int(version)}")


def _in_transaction(con: sqlite3.Connection, work: Callable[[], None]) -> None:
    con.execute("BEGIN IMMEDIATE")
    try:
        work()
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


def _apply(con: sqlite3.Connection, step: Step, note: str) -> None:
    def work() -> None:
        step.apply(con)
        if step.foreign_keys_off:
            broken = con.execute("PRAGMA foreign_key_check").fetchall()
            if broken:
                raise RuntimeError(f"schema step {step.version}: {len(broken)} broken foreign keys")
        _record(con, step.version, note)

    # PRAGMA foreign_keys is ignored inside a transaction, so it is switched around it.
    enforced = con.execute("PRAGMA foreign_keys").fetchone()[0]
    if step.foreign_keys_off:
        con.execute("PRAGMA foreign_keys=OFF")
    try:
        _in_transaction(con, work)
    finally:
        if step.foreign_keys_off:
            con.execute(f"PRAGMA foreign_keys={'ON' if enforced else 'OFF'}")


def _backup(con: sqlite3.Connection, db: Path, version: int) -> Path:
    dest = backup_path(db, version)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.unlink(missing_ok=True)
    out = sqlite3.connect(os.fspath(tmp))
    try:
        con.backup(out)
    finally:
        out.close()
    os.replace(tmp, dest)
    return dest


def ensure_schema(
    con: sqlite3.Connection,
    db: Path,
    create_current: Callable[[], None],
    steps: Sequence[Step] = STEPS,
) -> int:
    """Bring `db` to the newest step's version and return it.

    `con` must be idle (no open transaction). `create_current` builds the full current
    schema on an empty database (SQLAlchemy create_all).
    """
    current = steps[-1].version
    isolation = con.isolation_level
    con.isolation_level = None
    try:
        found = stored_version(con)
        if found > current:
            raise SchemaTooNew(db, found, current)
        if found == current:
            return current
        if found == 0 and not library_tables(con):
            create_current()
            _in_transaction(con, lambda: _record(con, current, "created"))
            _log.info("%s: created at schema v%d", db.name, current)
            return current

        start = found if found else guess_version(con)
        saved = _backup(con, db, start)
        for step in steps:
            if step.version <= found:
                continue
            note = step.note
            if found == 0 and step is steps[0]:
                note += f"; unversioned database, looked like v{start}"
            _apply(con, step, note)
        _log.info("%s: schema v%d -> v%d (copy kept as %s)", db.name, start, current, saved.name)
        return current
    finally:
        con.isolation_level = isolation
