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
]
