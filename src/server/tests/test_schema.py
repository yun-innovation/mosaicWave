from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from mosaicwave import schema
from mosaicwave.config import Settings
from mosaicwave.db import make_engine
from mosaicwave.schema.steps import STEPS, Step, column_names, rebuild_table
from mosaicwave.wipe import reset_library_db
from tests.authutil import authed_client

# Columns the pre-release code added by ALTER; their tables lacked them before.
LEGACY_TABLES = """
CREATE TABLE "user" (id VARCHAR(36) NOT NULL, provider VARCHAR(32) NOT NULL,
  provider_subject VARCHAR(255) NOT NULL, display_name VARCHAR(255) NOT NULL,
  role VARCHAR(32) NOT NULL, rev INTEGER NOT NULL, updated_at DATETIME NOT NULL,
  deleted_at DATETIME, PRIMARY KEY (id));
CREATE TABLE source (id VARCHAR(36) NOT NULL, backend VARCHAR(32) NOT NULL,
  root_uri TEXT NOT NULL, kind VARCHAR(32) NOT NULL, last_scan_at DATETIME, error TEXT,
  rev INTEGER NOT NULL, updated_at DATETIME NOT NULL, deleted_at DATETIME, PRIMARY KEY (id));
CREATE TABLE album (id VARCHAR(36) NOT NULL, name VARCHAR(255) NOT NULL,
  cover_asset_id VARCHAR(36), sort INTEGER NOT NULL, rev INTEGER NOT NULL,
  updated_at DATETIME NOT NULL, deleted_at DATETIME, PRIMARY KEY (id));
CREATE TABLE job (id VARCHAR(36) NOT NULL, kind VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL, created_at DATETIME NOT NULL, started_at DATETIME,
  finished_at DATETIME, input_json TEXT, result_json TEXT, error TEXT,
  processed INTEGER NOT NULL, total INTEGER NOT NULL, current TEXT, PRIMARY KEY (id));
"""

USER_ROW = (
    "INSERT INTO \"user\" (id, provider, provider_subject, display_name, role, rev, updated_at) "
    "VALUES ('u1', 'local', 'nasuser', 'Nas User', 'admin', 1, '2026-01-01')"
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)


def _open(settings: Settings) -> Path:
    make_engine(settings).dispose()
    return settings.database_path


def _write_db(path: Path, build) -> None:  # type: ignore[no-untyped-def]
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        build(con)
        con.commit()
    finally:
        con.close()


def _query(path: Path, sql: str) -> list[tuple]:
    con = sqlite3.connect(path)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _history(path: Path) -> list[tuple]:
    return _query(path, "SELECT version, note FROM schema_history ORDER BY rowid")


def _shape(path: Path) -> dict:
    """Tables with their columns, foreign keys and indexes, ignoring column order."""
    con = sqlite3.connect(path)
    try:
        shape = {}
        for table in sorted(schema.library_tables(con)):
            columns = {
                row[1]: (row[2].upper(), row[3], row[4], row[5])
                for row in con.execute(f'PRAGMA table_info("{table}")')
            }
            fks = {(row[2], row[3], row[4]) for row in con.execute(f'PRAGMA foreign_key_list("{table}")')}
            indexes = set()
            for row in con.execute(f'PRAGMA index_list("{table}")'):
                name, unique, partial = row[1], row[2], row[4]
                cols = tuple(r[2] for r in con.execute(f'PRAGMA index_info("{name}")'))
                label = "" if name.startswith("sqlite_autoindex") else name
                indexes.add((label, unique, partial, cols))
            shape[table] = {"columns": columns, "fks": fks, "indexes": indexes}
        return shape
    finally:
        con.close()


def test_new_database_is_created_at_current_version(tmp_path: Path) -> None:
    db = _open(_settings(tmp_path))
    assert _query(db, "PRAGMA user_version")[0][0] == schema.CURRENT_VERSION
    assert _history(db) == [(schema.CURRENT_VERSION, "created")]
    assert not list(db.parent.glob("library.db.pre-v*"))


def test_steps_build_the_same_schema_as_models(tmp_path: Path) -> None:
    """Fails when models.py changes without a matching step in schema/steps.py."""
    fresh = _open(_settings(tmp_path / "fresh"))
    stepped = tmp_path / "stepped.db"

    def build(con: sqlite3.Connection) -> None:
        for step in STEPS:
            step.apply(con)

    _write_db(stepped, build)
    assert _shape(stepped) == _shape(fresh)


def test_unversioned_v1_database_upgrades_and_keeps_rows(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = settings.database_path

    def build(con: sqlite3.Connection) -> None:
        STEPS[0].apply(con)
        con.execute(USER_ROW)

    _write_db(db, build)
    _open(settings)

    assert _query(db, "PRAGMA user_version")[0][0] == schema.CURRENT_VERSION
    assert _query(db, 'SELECT provider_subject, locale FROM "user"') == [("nasuser", None)]
    notes = _history(db)
    assert [v for v, _ in notes] == [1, 2]
    assert "looked like v1" in notes[0][1]
    backup = db.with_name("library.db.pre-v1")
    assert _query(backup, "PRAGMA user_version")[0][0] == 0
    assert "locale" not in {row[1] for row in _query(backup, 'PRAGMA table_info("user")')}


def test_unversioned_v2_database_is_numbered(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = settings.database_path

    def build(con: sqlite3.Connection) -> None:
        for step in STEPS:
            step.apply(con)

    _write_db(db, build)
    _open(settings)
    assert _query(db, "PRAGMA user_version")[0][0] == 2
    assert "looked like v2" in _history(db)[0][1]
    assert db.with_name("library.db.pre-v2").is_file()


def test_legacy_database_gets_missing_columns_tables_and_indexes(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = settings.database_path

    def build(con: sqlite3.Connection) -> None:
        con.executescript(LEGACY_TABLES)
        con.execute(USER_ROW)

    _write_db(db, build)
    _open(settings)

    assert _query(db, "PRAGMA user_version")[0][0] == schema.CURRENT_VERSION
    assert "looked like v0" in _history(db)[0][1]
    assert db.with_name("library.db.pre-v0").is_file()
    fresh = _shape(_open(_settings(tmp_path / "fresh")))
    upgraded = _shape(db)
    assert set(upgraded) == set(fresh)
    for table, parts in fresh.items():
        assert set(upgraded[table]["columns"]) == set(parts["columns"]), table
        assert upgraded[table]["indexes"] >= {i for i in parts["indexes"] if i[0]}, table
    assert _query(db, 'SELECT provider_subject FROM "user"') == [("nasuser",)]


def test_current_database_is_left_alone(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = _open(settings)
    _open(settings)
    assert len(_history(db)) == 1
    assert not list(db.parent.glob("library.db.pre-v*"))


def test_newer_database_is_refused_unchanged(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = _open(settings)
    _write_db(db, lambda con: con.execute("PRAGMA user_version = 99"))
    before = hashlib.sha256(db.read_bytes()).hexdigest()

    with pytest.raises(schema.SchemaTooNew) as caught:
        make_engine(settings)

    assert "schema v99" in str(caught.value)
    assert f"up to v{schema.CURRENT_VERSION}" in str(caught.value)
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_failed_step_rolls_back(tmp_path: Path) -> None:
    db = _open(_settings(tmp_path))

    def broken(con: sqlite3.Connection) -> None:
        con.execute("ALTER TABLE asset ADD COLUMN rating INTEGER")
        raise RuntimeError("step failed")

    con = sqlite3.connect(db)
    try:
        with pytest.raises(RuntimeError, match="step failed"):
            schema.ensure_schema(con, db, lambda: None, [*STEPS, Step(3, "rating", broken)])
        assert schema.stored_version(con) == 2
        assert "rating" not in column_names(con, "asset")
    finally:
        con.close()
    assert [v for v, _ in _history(db)] == [2]


def test_rebuild_step_keeps_rows_and_foreign_keys(tmp_path: Path) -> None:
    db = _open(_settings(tmp_path))
    _write_db(
        db,
        lambda con: con.executescript(
            "INSERT INTO job (id, kind, status, created_at, processed, total) "
            "VALUES ('j1', 'thumbs', 'done', '2026-01-01', 0, 0);"
            "INSERT INTO job_event (id, job_id, at, level, message) "
            "VALUES ('e1', 'j1', '2026-01-01', 'info', 'hello');"
        ),
    )

    def rebuild(con: sqlite3.Connection) -> None:
        rebuild_table(
            con,
            "job_event",
            "CREATE TABLE job_event__new (id VARCHAR(36) NOT NULL, job_id VARCHAR(36) NOT NULL, "
            "at DATETIME NOT NULL, level VARCHAR(16) NOT NULL, message TEXT NOT NULL, "
            "PRIMARY KEY (id), FOREIGN KEY(job_id) REFERENCES job (id))",
            ["id", "job_id", "at", "level", "message"],
        )
        con.execute("CREATE INDEX ix_job_event_job_id ON job_event (job_id)")

    con = sqlite3.connect(db)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        schema.ensure_schema(
            con, db, lambda: None, [*STEPS, Step(3, "rebuild", rebuild, foreign_keys_off=True)]
        )
        assert schema.stored_version(con) == 3
        assert con.execute("SELECT message FROM job_event").fetchall() == [("hello",)]
        assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        con.close()


def test_wipe_db_removes_upgrade_copies(tmp_path: Path) -> None:
    db = tmp_path / "library.db"
    _write_db(db, lambda con: con.execute("CREATE TABLE t (x)"))
    copy = db.with_name("library.db.pre-v1")
    copy.write_bytes(b"old")
    reset_library_db(db)
    assert not db.exists()
    assert not copy.exists()


def test_settings_reports_schema_version(tmp_path: Path) -> None:
    client = authed_client(_settings(tmp_path))
    res = client.get("/api/v1/settings")
    assert res.status_code == 200, res.text
    assert res.json()["schema_version"] == schema.CURRENT_VERSION
