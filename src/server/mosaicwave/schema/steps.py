"""Ordered upgrade steps for library.db. Append only; never edit a released step.

Steps 1 and 2 must stay safe to re-run: databases from before schema versions have no
number and may already hold part of them. Steps 3+ may assume the exact previous shape.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from mosaicwave.schema.v1 import V1_TABLES


@dataclass(frozen=True)
class Step:
    version: int
    note: str
    apply: Callable[[sqlite3.Connection], None]
    # Table rebuilds (create new, copy, drop, rename) need foreign keys off for the step.
    foreign_keys_off: bool = False


def column_names(con: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in con.execute(f'PRAGMA table_info("{table}")')}


def rebuild_table(con: sqlite3.Connection, table: str, create_sql: str, columns: list[str]) -> None:
    """Replace `table` with `create_sql` (which must create `{table}__new`), copying `columns`.

    Use from a step with foreign_keys_off=True. Recreate the table's indexes afterwards.
    """
    cols = ", ".join(f'"{c}"' for c in columns)
    con.execute(create_sql)
    con.execute(f'INSERT INTO "{table}__new" ({cols}) SELECT {cols} FROM "{table}"')
    con.execute(f'DROP TABLE "{table}"')
    con.execute(f'ALTER TABLE "{table}__new" RENAME TO "{table}"')


def _v1_baseline(con: sqlite3.Connection) -> None:
    for statement in V1_TABLES.split(";"):
        if statement.strip():
            con.execute(statement)
    # Pre-release databases got these columns by ALTER, without indexes.
    if "owner_user_id" not in column_names(con, "source"):
        con.execute('ALTER TABLE source ADD COLUMN owner_user_id VARCHAR(36) REFERENCES "user" (id)')
    if "source_id" not in column_names(con, "album"):
        con.execute("ALTER TABLE album ADD COLUMN source_id VARCHAR(36) REFERENCES source (id)")
    if "user_id" not in column_names(con, "job"):
        con.execute('ALTER TABLE job ADD COLUMN user_id VARCHAR(36) REFERENCES "user" (id)')
    con.execute("CREATE INDEX IF NOT EXISTS ix_source_owner_user_id ON source (owner_user_id)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_job_user_id ON job (user_id)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_job_event_job_id ON job_event (job_id)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_album_source_id ON album (source_id)")
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_source_owner_live ON source (owner_user_id) "
        "WHERE deleted_at IS NULL AND owner_user_id IS NOT NULL"
    )


def _user_locale(con: sqlite3.Connection) -> None:
    if "locale" not in column_names(con, "user"):
        con.execute('ALTER TABLE "user" ADD COLUMN locale VARCHAR(16)')


def _job_text_codes(con: sqlite3.Connection) -> None:
    """Step 4: a job's error, current line and events as code + params (milestone 8)."""
    wanted = {
        "job": [
            ("error_code", "VARCHAR(64)"),
            ("error_params", "TEXT"),
            ("current_code", "VARCHAR(64)"),
            ("current_params", "TEXT"),
        ],
        "job_event": [("code", "VARCHAR(64)"), ("params", "TEXT")],
    }
    for table, columns in wanted.items():
        have = column_names(con, table)
        for name, kind in columns:
            if name not in have:
                con.execute(f'ALTER TABLE "{table}" ADD COLUMN {name} {kind}')


def _user_time_zone(con: sqlite3.Connection) -> None:
    """Step 5: the zone a user wants times shown in, an IANA name or NULL for UTC (milestone 8)."""
    if "time_zone" not in column_names(con, "user"):
        con.execute('ALTER TABLE "user" ADD COLUMN time_zone VARCHAR(64)')


def _user_use_dst(con: sqlite3.Connection) -> None:
    """Step 6: whether a user's time zone follows summer time; 0 = standard time all year (milestone 8)."""
    if "use_dst" not in column_names(con, "user"):
        con.execute('ALTER TABLE "user" ADD COLUMN use_dst BOOLEAN NOT NULL DEFAULT 0')


def _sessions_and_site_settings(con: sqlite3.Connection) -> None:
    """Step 7: stored sign-ins (`user_session`) and admin-editable server settings (`site_setting`).

    Sessions that were signed values with no row (the user id was the subject) stop working: people sign in again.
    """
    con.execute(
        "CREATE TABLE IF NOT EXISTS user_session ("
        "id VARCHAR(36) NOT NULL PRIMARY KEY, "
        "user_id VARCHAR(36) NOT NULL REFERENCES \"user\" (id), "
        "kind VARCHAR(16) NOT NULL, device_name VARCHAR(100), "
        "created_at DATETIME NOT NULL, last_seen_at DATETIME NOT NULL, "
        "expires_at DATETIME NOT NULL, revoked_at DATETIME)"
    )
    con.execute("CREATE INDEX IF NOT EXISTS ix_user_session_user_id ON user_session (user_id)")
    con.execute(
        "CREATE TABLE IF NOT EXISTS site_setting ("
        "key VARCHAR(64) NOT NULL PRIMARY KEY, value TEXT NOT NULL, updated_at DATETIME NOT NULL)"
    )


def _drop_moved_tables(con: sqlite3.Connection) -> None:
    """Step 3 only runs after schema/split.py has copied every library out (see split.py)."""
    for table in ("album_asset", "album", "asset", "source_grant", "source"):
        con.execute(f'DROP TABLE IF EXISTS "{table}"')


STEPS: list[Step] = [
    Step(1, "baseline (v0.1.0 / v0.1.1 tables)", _v1_baseline),
    Step(2, "user.locale (milestone 3)", _user_locale),
    Step(
        3,
        "per-library databases: source, asset, album, album_asset moved to accounts/{id}/library.db; "
        "source_grant dropped",
        _drop_moved_tables,
        foreign_keys_off=True,
    ),
    Step(4, "job.error_code/error_params/current_code/current_params, job_event.code/params (milestone 8)", _job_text_codes),
    Step(5, "user.time_zone (milestone 8)", _user_time_zone),
    Step(6, "user.use_dst (milestone 8)", _user_use_dst),
    Step(7, "user_session and site_setting tables (milestone 14)", _sessions_and_site_settings),
]
