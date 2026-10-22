"""Schema track for a per-user library database (accounts/{user_id}/library.db).

Separate from the site database's steps (steps.py): own versions, own `schema_history`.
A library database holds `source`, `asset`, `album` and `album_asset` and nothing that
points at another file, so `source.owner_user_id` is a plain id (no foreign key to `user`).

Append only; never edit a released step. Steps must be safe to re-run.
"""

from __future__ import annotations

import sqlite3

from mosaicwave.schema.steps import Step

LIBRARY_V1_TABLES = """CREATE TABLE IF NOT EXISTS source (
	id VARCHAR(36) NOT NULL,
	owner_user_id VARCHAR(36),
	backend VARCHAR(32) NOT NULL,
	root_uri TEXT NOT NULL,
	kind VARCHAR(32) NOT NULL,
	last_scan_at DATETIME,
	error TEXT,
	rev INTEGER NOT NULL,
	updated_at DATETIME NOT NULL,
	deleted_at DATETIME,
	PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS asset (
	id VARCHAR(36) NOT NULL,
	source_id VARCHAR(36) NOT NULL,
	relative_path TEXT NOT NULL,
	size INTEGER NOT NULL,
	mtime FLOAT NOT NULL,
	mime VARCHAR(127),
	width INTEGER,
	height INTEGER,
	taken_at DATETIME,
	duration FLOAT,
	extra_json TEXT,
	content_hash VARCHAR(64),
	thumb_rev INTEGER NOT NULL,
	rev INTEGER NOT NULL,
	updated_at DATETIME NOT NULL,
	deleted_at DATETIME,
	PRIMARY KEY (id),
	CONSTRAINT uq_asset_source_path UNIQUE (source_id, relative_path),
	FOREIGN KEY(source_id) REFERENCES source (id)
);

CREATE TABLE IF NOT EXISTS album (
	id VARCHAR(36) NOT NULL,
	source_id VARCHAR(36),
	name VARCHAR(255) NOT NULL,
	cover_asset_id VARCHAR(36),
	sort INTEGER NOT NULL,
	rev INTEGER NOT NULL,
	updated_at DATETIME NOT NULL,
	deleted_at DATETIME,
	PRIMARY KEY (id),
	CONSTRAINT uq_album_source_name UNIQUE (source_id, name),
	FOREIGN KEY(source_id) REFERENCES source (id),
	FOREIGN KEY(cover_asset_id) REFERENCES asset (id)
);

CREATE TABLE IF NOT EXISTS album_asset (
	id VARCHAR(36) NOT NULL,
	album_id VARCHAR(36) NOT NULL,
	asset_id VARCHAR(36) NOT NULL,
	position INTEGER NOT NULL,
	rev INTEGER NOT NULL,
	updated_at DATETIME NOT NULL,
	deleted_at DATETIME,
	PRIMARY KEY (id),
	CONSTRAINT uq_album_asset UNIQUE (album_id, asset_id),
	FOREIGN KEY(album_id) REFERENCES album (id),
	FOREIGN KEY(asset_id) REFERENCES asset (id)
);

CREATE INDEX IF NOT EXISTS ix_source_owner_user_id ON source (owner_user_id);
CREATE INDEX IF NOT EXISTS ix_album_source_id ON album (source_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_source_owner_live ON source (owner_user_id)
	WHERE deleted_at IS NULL AND owner_user_id IS NOT NULL;
"""


def _library_v1(con: sqlite3.Connection) -> None:
    for statement in LIBRARY_V1_TABLES.split(";"):
        if statement.strip():
            con.execute(statement)


LIBRARY_STEPS: list[Step] = [
    Step(1, "library database baseline (source, asset, album, album_asset)", _library_v1),
]

LIBRARY_CURRENT_VERSION = LIBRARY_STEPS[-1].version


def create_library_schema(con: sqlite3.Connection) -> None:
    """Build the current schema on an empty file by running every step in order."""
    con.execute("BEGIN IMMEDIATE")
    try:
        for step in LIBRARY_STEPS:
            step.apply(con)
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
