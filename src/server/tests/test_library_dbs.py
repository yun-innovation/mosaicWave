from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import select

from mosaicwave import schema
from mosaicwave.config import Settings
from mosaicwave.db import open_engine
from mosaicwave.library.dbs import LibraryDbs
from mosaicwave.library.sync import assign_sync_rev, max_sync_rev
from mosaicwave.main import create_app
from mosaicwave.models import LIBRARY_TABLES, SITE_TABLES, Asset, Base, Source
from mosaicwave.schema.library import LIBRARY_CURRENT_VERSION, LIBRARY_STEPS, create_library_schema
from mosaicwave.schema.steps import Step
from sqlalchemy import create_engine

from test_schema import _shape


def _settings(tmp_path: Path, **kw) -> Settings:  # type: ignore[no-untyped-def]
    return Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None, **kw)


def _query(path: Path, sql: str) -> list[tuple]:
    con = sqlite3.connect(path)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _add_asset(dbs: LibraryDbs, user_id: str, name: str) -> None:
    with dbs.factory(user_id)() as session:
        source = session.scalar(select(Source))
        if source is None:
            source = Source(owner_user_id=user_id, root_uri="mw-data:x", kind="folder")
            session.add(source)
            session.flush()
        row = Asset(source_id=source.id, relative_path=name)
        assign_sync_rev(session, row)
        session.add(row)
        session.commit()


def test_every_table_belongs_to_exactly_one_database() -> None:
    assert SITE_TABLES | LIBRARY_TABLES == set(Base.metadata.tables)
    assert not SITE_TABLES & LIBRARY_TABLES


def test_library_schema_matches_the_models(tmp_path: Path) -> None:
    """Fails when a library table changes in models.py without a library step."""
    modeled = tmp_path / "modeled.db"
    engine = create_engine(f"sqlite:///{modeled.as_posix()}")
    Base.metadata.create_all(engine, tables=[Base.metadata.tables[t] for t in LIBRARY_TABLES])
    engine.dispose()

    stepped = tmp_path / "stepped.db"
    con = sqlite3.connect(stepped, isolation_level=None)
    create_library_schema(con)
    con.close()

    want, got = _shape(modeled), _shape(stepped)
    assert set(got) == LIBRARY_TABLES
    for table in LIBRARY_TABLES:
        # The models still point source.owner_user_id at "user"; a library file has no user table.
        want[table]["fks"] = {fk for fk in want[table]["fks"] if fk[0] in LIBRARY_TABLES}
    assert got == want


def test_library_file_points_at_nothing_outside_itself(tmp_path: Path) -> None:
    dbs = LibraryDbs(_settings(tmp_path))
    path = dbs.path("u1")
    dbs.engine("u1")
    con = sqlite3.connect(path)
    try:
        for table in LIBRARY_TABLES:
            for fk in con.execute(f'PRAGMA foreign_key_list("{table}")'):
                assert fk[2] in LIBRARY_TABLES
    finally:
        con.close()


def test_accounts_dir_sits_beside_the_site_database(tmp_path: Path) -> None:
    boot = tmp_path / "app"
    settings = _settings(tmp_path, bootstrap_dir=boot)
    assert settings.accounts_dir == settings.database_path.parent / "accounts"
    assert settings.accounts_dir.parent == boot
    assert not str(settings.accounts_dir).startswith(str(settings.data_dir))


def test_first_use_creates_the_file_at_the_current_version(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    dbs = LibraryDbs(settings)
    assert dbs.user_ids() == [] and not dbs.exists("u1")

    dbs.engine("u1")
    path = dbs.path("u1")
    assert path == settings.accounts_dir / "u1" / "library.db" and path.is_file()
    assert _query(path, "PRAGMA user_version")[0][0] == LIBRARY_CURRENT_VERSION
    assert _query(path, "SELECT version, note FROM schema_history") == [(LIBRARY_CURRENT_VERSION, "created")]
    assert dbs.user_ids() == ["u1"]
    assert not list(path.parent.glob("library.db.pre-v*"))


def test_rows_survive_a_reopen_and_libraries_are_isolated(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    dbs = LibraryDbs(settings)
    _add_asset(dbs, "alice", "a.jpg")
    _add_asset(dbs, "alice", "b.jpg")
    _add_asset(dbs, "alice", "c.jpg")
    _add_asset(dbs, "bob", "x.jpg")
    dbs.dispose_all()
    assert dbs.open_count() == 0

    reopened = LibraryDbs(settings)
    with reopened.factory("alice")() as session:
        assert {a.relative_path for a in session.scalars(select(Asset))} == {"a.jpg", "b.jpg", "c.jpg"}
        assert max_sync_rev(session) == 3
    with reopened.factory("bob")() as session:
        assert [a.relative_path for a in session.scalars(select(Asset))] == ["x.jpg"]
        assert max_sync_rev(session) == 1  # bob's clock never saw alice's three changes
    assert reopened.user_ids() == ["alice", "bob"]


@pytest.mark.parametrize("bad", ["", "..", "../x", "a/b", "a\\b", ".hidden", "x" * 65])
def test_user_ids_cannot_escape_the_accounts_folder(tmp_path: Path, bad: str) -> None:
    dbs = LibraryDbs(_settings(tmp_path))
    with pytest.raises(ValueError):
        dbs.engine(bad)
    assert not (tmp_path / "data").exists() or not list(tmp_path.rglob("library.db"))


def test_engine_cache_is_bounded(tmp_path: Path) -> None:
    dbs = LibraryDbs(_settings(tmp_path), max_open=2)
    for user in ("u1", "u2", "u3"):
        _add_asset(dbs, user, f"{user}.jpg")
    assert dbs.open_count() == 2
    _add_asset(dbs, "u1", "again.jpg")  # evicted earlier, reopens with its rows intact
    with dbs.factory("u1")() as session:
        assert session.scalar(select(Asset).where(Asset.relative_path == "u1.jpg")) is not None
    assert dbs.open_count() == 2
    with pytest.raises(ValueError):
        LibraryDbs(_settings(tmp_path), max_open=0)


def test_newer_library_file_is_refused_and_left_alone(tmp_path: Path) -> None:
    dbs = LibraryDbs(_settings(tmp_path))
    dbs.engine("u1")
    dbs.dispose_all()
    path = dbs.path("u1")
    con = sqlite3.connect(path)
    con.execute("PRAGMA user_version = 99")
    con.commit()
    con.close()
    before = path.read_bytes()
    with pytest.raises(schema.SchemaTooNew):
        dbs.engine("u1")
    assert path.read_bytes() == before
    assert not list(path.parent.glob("library.db.pre-v*"))


def test_older_library_file_upgrades_with_a_copy(tmp_path: Path) -> None:
    dbs = LibraryDbs(_settings(tmp_path))
    _add_asset(dbs, "u1", "keep.jpg")
    dbs.dispose_all()
    path = dbs.path("u1")

    def add_note(con: sqlite3.Connection) -> None:
        con.execute("ALTER TABLE asset ADD COLUMN note TEXT")

    steps = [*LIBRARY_STEPS, Step(LIBRARY_CURRENT_VERSION + 1, "asset.note", add_note)]
    open_engine(path, steps=steps, create_current=lambda e: None).dispose()

    assert _query(path, "PRAGMA user_version")[0][0] == LIBRARY_CURRENT_VERSION + 1
    assert _query(path, "SELECT relative_path FROM asset") == [("keep.jpg",)]
    copy = path.with_name(f"library.db.pre-v{LIBRARY_CURRENT_VERSION}")
    assert copy.is_file()
    assert _query(copy, "PRAGMA user_version")[0][0] == LIBRARY_CURRENT_VERSION


def test_app_has_the_manager_but_creates_no_library_files_yet(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    assert isinstance(app.state.library_dbs, LibraryDbs)
    assert app.state.library_dbs.user_ids() == []
    assert not settings.accounts_dir.exists()
