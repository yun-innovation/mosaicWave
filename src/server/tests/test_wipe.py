from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mosaicwave.config import Settings
from mosaicwave.wipe import (
    EXPECTED_TABLES,
    init_library_db,
    main,
    table_names,
    wipe_storage,
)


def test_init_library_db_creates_tables(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    path = init_library_db(settings)
    assert path == tmp_path / "data" / "library.db"
    assert path.is_file()
    assert EXPECTED_TABLES <= table_names(path)


def test_init_reset_drops_rows_keeps_schema(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    path = init_library_db(settings)
    con = sqlite3.connect(path)
    con.execute(
        "INSERT INTO user (id, provider, provider_subject, display_name, role, rev, updated_at) "
        "VALUES ('u1', 'local', 'pat', 'Pat', 'member', 1, '2026-01-01T00:00:00Z')"
    )
    con.commit()
    con.close()
    init_library_db(settings, reset=True)
    con = sqlite3.connect(path)
    count = con.execute("SELECT COUNT(*) FROM user").fetchone()[0]
    con.close()
    assert count == 0
    assert EXPECTED_TABLES <= table_names(path)


def test_wipe_storage_removes_libraries_and_blobs(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    (data_dir / "libraries" / "u1").mkdir(parents=True)
    (data_dir / "libraries" / "u1" / "photo.jpg").write_bytes(b"x")
    (data_dir / "blobs" / "ab").mkdir(parents=True)
    (data_dir / "blobs" / "ab" / "thumb.jpg").write_bytes(b"y")
    wipe_storage(data_dir)
    assert not (data_dir / "libraries").exists()
    assert not (data_dir / "blobs").exists()


def test_wipe_storage_missing_dirs_is_noop(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    wipe_storage(data_dir)  # does not raise


def test_init_library_db_wipe_leaves_db_rows_unless_reset(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    path = init_library_db(settings)
    (settings.data_dir / "libraries" / "u1").mkdir(parents=True)
    con = sqlite3.connect(path)
    con.execute(
        "INSERT INTO user (id, provider, provider_subject, display_name, role, rev, updated_at) "
        "VALUES ('u1', 'local', 'pat', 'Pat', 'member', 1, '2026-01-01T00:00:00Z')"
    )
    con.commit()
    con.close()
    init_library_db(settings, wipe=True)
    assert not (settings.data_dir / "libraries").exists()
    con = sqlite3.connect(path)
    count = con.execute("SELECT COUNT(*) FROM user").fetchone()[0]
    con.close()
    assert count == 1


def test_wipe_cli_no_flags_wipes_both(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dest = tmp_path / "lib"
    monkeypatch.setenv("MOSAICWAVE_DATA_DIR", str(dest))
    # Seed: db row + storage files, as if the app had already run.
    assert main([]) == 0
    con = sqlite3.connect(dest / "library.db")
    con.execute(
        "INSERT INTO user (id, provider, provider_subject, display_name, role, rev, updated_at) "
        "VALUES ('u1', 'local', 'pat', 'Pat', 'member', 1, '2026-01-01T00:00:00Z')"
    )
    con.commit()
    con.close()
    (dest / "blobs").mkdir(parents=True)
    (dest / "blobs" / "x.jpg").write_bytes(b"z")

    assert main([]) == 0  # no flags: wipe both

    assert not (dest / "blobs").exists()
    con = sqlite3.connect(dest / "library.db")
    count = con.execute("SELECT COUNT(*) FROM user").fetchone()[0]
    con.close()
    assert count == 0


def test_wipe_cli_db_only_keeps_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dest = tmp_path / "lib"
    monkeypatch.setenv("MOSAICWAVE_DATA_DIR", str(dest))
    assert main([]) == 0
    (dest / "blobs").mkdir(parents=True)
    (dest / "blobs" / "x.jpg").write_bytes(b"z")

    assert main(["--db"]) == 0

    assert (dest / "blobs" / "x.jpg").is_file()


def test_wipe_cli_storage_only_keeps_db_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "lib"
    monkeypatch.setenv("MOSAICWAVE_DATA_DIR", str(dest))
    assert main([]) == 0
    con = sqlite3.connect(dest / "library.db")
    con.execute(
        "INSERT INTO user (id, provider, provider_subject, display_name, role, rev, updated_at) "
        "VALUES ('u1', 'local', 'pat', 'Pat', 'member', 1, '2026-01-01T00:00:00Z')"
    )
    con.commit()
    con.close()
    (dest / "blobs").mkdir(parents=True)
    (dest / "blobs" / "x.jpg").write_bytes(b"z")

    assert main(["--storage"]) == 0

    assert not (dest / "blobs").exists()
    con = sqlite3.connect(dest / "library.db")
    count = con.execute("SELECT COUNT(*) FROM user").fetchone()[0]
    con.close()
    assert count == 1


def test_wipe_cli_has_no_data_dir_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOSAICWAVE_DATA_DIR", str(tmp_path / "lib"))
    with pytest.raises(SystemExit):
        main(["--data-dir", str(tmp_path / "elsewhere")])
