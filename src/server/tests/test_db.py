from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from mosaicwave.config import Settings
from mosaicwave.db import make_engine, sqlite_locked


def test_overlapping_sessions_survive_close(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    engine = make_engine(settings)
    factory = sessionmaker(engine, expire_on_commit=False)
    first = factory()
    second = factory()
    try:
        assert first.scalar(text("SELECT 1")) == 1
        assert second.scalar(text("SELECT 1")) == 1
        first.close()
        assert second.scalar(text("SELECT 1")) == 1
    finally:
        second.close()
        engine.dispose()


def test_sqlite_locked_detects_wrapped_error() -> None:
    import sqlite3

    from sqlalchemy.exc import OperationalError

    inner = sqlite3.OperationalError("database is locked")
    wrapped = OperationalError("UPDATE asset", {}, inner)
    assert sqlite_locked(wrapped) is True
    assert sqlite_locked(inner) is True
    assert sqlite_locked(ValueError("no")) is False
