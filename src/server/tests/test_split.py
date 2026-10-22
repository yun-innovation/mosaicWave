from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from mosaicwave import schema
from mosaicwave.config import Settings
from mosaicwave.models import (
    Album,
    AlbumAsset,
    Asset,
    Job,
    LocalCredential,
    Source,
    User,
    utcnow,
)
from mosaicwave.schema import split
from mosaicwave.schema.library import LIBRARY_CURRENT_VERSION
from mosaicwave.schema.split import REPORT_NAME, SplitError, migrate_site_database, split_site_database
from mosaicwave.schema.steps import STEPS

SITE_AFTER = {"user", "local_credential", "job", "job_event"}


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)


def _query(path: Path, sql: str) -> list[tuple]:
    con = sqlite3.connect(path)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _tables(path: Path) -> set[str]:
    con = sqlite3.connect(path)
    try:
        return schema.library_tables(con)
    finally:
        con.close()


def _seed_v2_schema(settings: Settings) -> dict[str, str]:
    """An empty database exactly as v0.1.x / milestone 3 left it (schema version 2)."""
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(settings.database_path)
    try:
        for step in STEPS[:2]:
            step.apply(con)
        con.execute("PRAGMA user_version = 2")
        con.commit()
    finally:
        con.close()
    return {}


def _seed(settings: Settings) -> dict[str, str]:
    """Two users with libraries, plus the awkward rows a real database can hold."""
    ids = _seed_v2_schema(settings)
    engine = create_engine(f"sqlite:///{settings.database_path}")
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as s:
        alice = User(provider="local", provider_subject="alice", display_name="Alice", role="admin")
        bob = User(provider="local", provider_subject="bob", display_name="Bob")
        gone = User(provider="local", provider_subject="gone", display_name="Gone", deleted_at=utcnow())
        s.add_all([alice, bob, gone])
        s.flush()
        s.add_all([LocalCredential(user_id=alice.id, password_hash="x"), Job(kind="takeout_import", user_id=bob.id)])
        ids.update(alice=alice.id, bob=bob.id)

        a_src = Source(owner_user_id=alice.id, root_uri="/data/libraries/a", kind="folder")
        a_old = Source(owner_user_id=alice.id, root_uri="/data/libraries/a-old", kind="folder", deleted_at=utcnow())
        b_src = Source(owner_user_id=bob.id, root_uri="/data/libraries/b", kind="folder")
        stray = Source(owner_user_id=None, root_uri="/data/stray", kind="folder")
        s.add_all([a_src, a_old, b_src, stray])
        s.flush()

        def asset(src: Source, name: str, rev: int, **kw) -> Asset:  # type: ignore[no-untyped-def]
            row = Asset(source_id=src.id, relative_path=name, rev=rev, **kw)
            s.add(row)
            return row

        a1, a2 = asset(a_src, "a1.jpg", 5), asset(a_src, "a2.jpg", 9)
        asset(a_old, "old.jpg", 2, deleted_at=utcnow())
        b1 = asset(b_src, "b1.jpg", 7)
        st = asset(stray, "stray.jpg", 3)
        s.flush()

        trip = Album(source_id=a_src.id, name="Trip", cover_asset_id=a1.id, rev=11)
        borrowed = Album(source_id=a_src.id, name="Borrowed cover", cover_asset_id=b1.id, rev=12)
        bobs = Album(source_id=b_src.id, name="Bob's", rev=4)
        lost = Album(source_id=None, name="Nobody's", rev=1)
        s.add_all([trip, borrowed, bobs, lost])
        s.flush()
        s.add_all(
            [
                AlbumAsset(album_id=trip.id, asset_id=a1.id, rev=13),
                AlbumAsset(album_id=trip.id, asset_id=a2.id, rev=14),
                AlbumAsset(album_id=trip.id, asset_id=b1.id, rev=15),  # another user's photo
                AlbumAsset(album_id=bobs.id, asset_id=b1.id, rev=6),
                AlbumAsset(album_id=lost.id, asset_id=a1.id, rev=1),  # album nobody owns
                AlbumAsset(album_id=trip.id, asset_id=st.id, rev=2),  # asset nobody owns
            ]
        )
        s.commit()
        s.execute(
            text(
                "INSERT INTO source_grant (id, user_id, source_id, perm, rev, updated_at) "
                "VALUES ('g1', :u, :s, 'read', 1, CURRENT_TIMESTAMP)"
            ),
            {"u": bob.id, "s": a_src.id},
        )
        s.commit()
    engine.dispose()
    return ids


def test_each_user_gets_a_library_with_their_rows_and_clock(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    ids = _seed(settings)
    report = migrate_site_database(settings.database_path, settings.accounts_dir)
    assert report is not None

    alice = settings.accounts_dir / ids["alice"] / "library.db"
    bob = settings.accounts_dir / ids["bob"] / "library.db"
    assert sorted(report.users) == sorted([ids["alice"], ids["bob"]])

    # Alice: live and tombstoned source, three assets (one tombstone), two albums.
    assert _query(alice, "SELECT count(*) FROM source") == [(2,)]
    assert {r[0] for r in _query(alice, "SELECT relative_path FROM asset")} == {"a1.jpg", "a2.jpg", "old.jpg"}
    assert _query(alice, "SELECT deleted_at IS NOT NULL FROM asset WHERE relative_path='old.jpg'") == [(1,)]
    assert {r[0] for r in _query(alice, "SELECT name FROM album")} == {"Trip", "Borrowed cover"}
    # rev values are kept, so each library's clock continues from its own maximum.
    assert _query(alice, "SELECT max(rev) FROM asset") == [(9,)]
    assert _query(alice, "SELECT max(rev) FROM album_asset") == [(14,)]
    assert _query(bob, "SELECT max(rev) FROM asset") == [(7,)]
    assert _query(bob, "SELECT relative_path FROM asset") == [("b1.jpg",)]
    assert _query(bob, "SELECT count(*) FROM album_asset") == [(1,)]

    for db in (alice, bob):
        assert _query(db, "PRAGMA user_version") == [(LIBRARY_CURRENT_VERSION,)]
        assert _query(db, "PRAGMA foreign_key_check") == []
        assert "source_grant" not in _tables(db) and "user" not in _tables(db)


def test_rows_that_cannot_move_are_reported_not_lost_silently(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    ids = _seed(settings)
    report = migrate_site_database(settings.database_path, settings.accounts_dir)
    assert report is not None
    assert report.orphan_sources == 1  # the ownerless source
    assert report.orphan_assets == 1
    assert report.orphan_albums == 1
    assert report.orphan_album_assets == 2  # lost-album row and stray-asset row
    assert report.cross_library_album_assets == 1  # Trip holding Bob's photo
    assert report.grants_dropped == 1
    assert report.covers_cleared == 1

    alice = settings.accounts_dir / ids["alice"] / "library.db"
    assert _query(alice, "SELECT cover_asset_id FROM album WHERE name='Borrowed cover'") == [(None,)]
    assert _query(alice, "SELECT cover_asset_id IS NOT NULL FROM album WHERE name='Trip'") == [(1,)]

    saved = json.loads(settings.database_path.with_name(REPORT_NAME).read_text(encoding="utf-8"))
    assert saved["orphan_albums"] == 1 and saved["grants_dropped"] == 1


def test_site_database_keeps_accounts_and_jobs_only(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    ids = _seed(settings)
    migrate_site_database(settings.database_path, settings.accounts_dir)
    db = settings.database_path
    assert _tables(db) == SITE_AFTER
    assert _query(db, "PRAGMA user_version") == [(split.SPLIT_VERSION,)]
    assert _query(db, "SELECT count(*) FROM user") == [(3,)]
    assert _query(db, f"SELECT password_hash FROM local_credential WHERE user_id='{ids['alice']}'") == [("x",)]
    assert _query(db, "SELECT count(*) FROM job") == [(1,)]
    assert _query(db, "SELECT version FROM schema_history ORDER BY rowid")[-1] == (split.SPLIT_VERSION,)
    # The pre-upgrade copy still holds everything.
    copy = db.with_name("library.db.pre-v2")
    assert copy.is_file() and "asset" in _tables(copy)
    assert _query(copy, "SELECT count(*) FROM asset") == [(5,)]


def test_running_it_again_does_nothing(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    ids = _seed(settings)
    assert migrate_site_database(settings.database_path, settings.accounts_dir) is not None
    lib = settings.accounts_dir / ids["alice"] / "library.db"
    before = lib.read_bytes()
    assert migrate_site_database(settings.database_path, settings.accounts_dir) is None
    assert lib.read_bytes() == before


def test_fresh_and_missing_databases_are_left_alone(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert migrate_site_database(settings.database_path, settings.accounts_dir) is None
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(settings.database_path).close()  # empty file, as a first start would leave
    assert migrate_site_database(settings.database_path, settings.accounts_dir) is None
    assert not settings.accounts_dir.exists()


def test_interrupted_run_is_redone_from_the_site_tables(tmp_path: Path) -> None:
    """Crash after the libraries were built but before the drop: site tables still rule."""
    settings = _settings(tmp_path)
    ids = _seed(settings)
    split_site_database(settings.database_path, settings.accounts_dir)  # stops before the drop
    assert "asset" in _tables(settings.database_path)
    stale = settings.accounts_dir / ids["alice"] / "library.db"
    con = sqlite3.connect(stale)
    con.execute("DELETE FROM asset")  # a half-baked file must not survive a rerun
    con.commit()
    con.close()

    report = migrate_site_database(settings.database_path, settings.accounts_dir)
    assert report is not None
    assert _query(stale, "SELECT count(*) FROM asset") == [(3,)]
    assert _tables(settings.database_path) == SITE_AFTER


def test_failure_changes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    ids = _seed(settings)
    real = split._build_library
    calls: list[str] = []

    def flaky(site: Path, user_id: str, target: Path):  # type: ignore[no-untyped-def]
        calls.append(user_id)
        if len(calls) == 2:
            raise SplitError("disk went away")
        return real(site, user_id, target)

    monkeypatch.setattr(split, "_build_library", flaky)
    with pytest.raises(SplitError):
        migrate_site_database(settings.database_path, settings.accounts_dir)

    db = settings.database_path
    assert _query(db, "PRAGMA user_version") == [(2,)]
    assert {"asset", "album", "source", "source_grant"} <= _tables(db)
    assert not list(settings.accounts_dir.rglob("library.db*"))
    assert ids["alice"] in calls


def test_unversioned_v1_database_is_migrated_too(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = settings.database_path
    db.parent.mkdir(parents=True)
    con = sqlite3.connect(db)
    STEPS[0].apply(con)  # the v0.1.0 / v0.1.1 shape: no version number, no user.locale
    con.execute(
        "INSERT INTO \"user\" (id, provider, provider_subject, display_name, role, rev, updated_at) "
        "VALUES ('u1', 'local', 'nasuser', 'Nas User', 'admin', 1, '2026-01-01')"
    )
    con.execute(
        "INSERT INTO source (id, owner_user_id, backend, root_uri, kind, rev, updated_at) "
        "VALUES ('s1', 'u1', 'local', '/x', 'folder', 1, '2026-01-01')"
    )
    con.execute(
        "INSERT INTO asset (id, source_id, relative_path, size, mtime, thumb_rev, rev, updated_at) "
        "VALUES ('a1', 's1', 'p.jpg', 1, 0, 0, 3, '2026-01-01')"
    )
    con.commit()
    con.close()

    report = migrate_site_database(db, settings.accounts_dir)
    assert report is not None and report.users["u1"].assets == 1
    assert _query(settings.accounts_dir / "u1" / "library.db", "SELECT rev FROM asset") == [(3,)]
    assert _tables(db) == SITE_AFTER
    assert "locale" in {r[1] for r in _query(db, 'PRAGMA table_info("user")')}


def test_newer_site_database_is_refused(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = settings.database_path
    db.parent.mkdir(parents=True)
    con = sqlite3.connect(db)
    con.execute("PRAGMA user_version = 9")
    con.commit()
    con.close()
    with pytest.raises(schema.SchemaTooNew):
        migrate_site_database(db, settings.accounts_dir)
