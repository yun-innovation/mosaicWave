from __future__ import annotations

import os
import sqlite3
from collections.abc import Generator
from pathlib import Path

from fastapi import Request
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from mosaicwave.config import Settings, ensure_data_dir_exists
from mosaicwave.models import Base


def _column_names(conn, table: str) -> set[str]:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1] for row in rows}


def _migrate_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        if "source" in {
            row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }:
            if "owner_user_id" not in _column_names(conn, "source"):
                conn.execute(text("ALTER TABLE source ADD COLUMN owner_user_id VARCHAR(36)"))
            if "source_id" not in _column_names(conn, "album"):
                conn.execute(text("ALTER TABLE album ADD COLUMN source_id VARCHAR(36)"))
            if "user_id" not in _column_names(conn, "job"):
                conn.execute(text("ALTER TABLE job ADD COLUMN user_id VARCHAR(36)"))
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_source_owner_live "
                    "ON source(owner_user_id) WHERE deleted_at IS NULL AND owner_user_id IS NOT NULL"
                )
            )


def _is_network_path(path: Path) -> bool:
    raw = os.fspath(path)
    posix = Path(raw).as_posix()
    if raw.startswith("\\\\") or posix.startswith("//"):
        return True
    return posix.startswith("/Volumes/")


def _sqlite_file_uri(path: Path, query: str) -> str:
    posix = path.resolve().as_posix()
    return f"file://{posix}?{query}"


def _connect_sqlite(path: Path, *, nolock: bool = False) -> sqlite3.Connection:
    raw = os.fspath(path.resolve())
    if nolock:
        uri = _sqlite_file_uri(path, "mode=rwc&nolock=1")
        return sqlite3.connect(uri, uri=True, timeout=30.0, check_same_thread=False)
    return sqlite3.connect(raw, timeout=30.0, check_same_thread=False)


def _configure_engine(engine: Engine, *, network: bool) -> None:
    @event.listens_for(engine, "connect")
    def _sqlite_pragma(dbapi_conn, _connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        if network:
            cursor.execute("PRAGMA journal_mode=DELETE")
        else:
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
                row = cursor.fetchone()
                mode = (row[0] if row else "") or ""
                if str(mode).lower() != "wal":
                    cursor.execute("PRAGMA journal_mode=DELETE")
            except Exception:
                cursor.execute("PRAGMA journal_mode=DELETE")
        cursor.close()


def make_engine(settings: Settings) -> Engine:
    ensure_data_dir_exists(settings.data_dir)
    if settings.bootstrap_dir is not None:
        ensure_data_dir_exists(Path(settings.bootstrap_dir))
    db_path = settings.database_path
    network = _is_network_path(db_path)

    def _engine(*, nolock: bool) -> Engine:
        engine = create_engine(
            "sqlite+pysqlite://",
            creator=lambda: _connect_sqlite(db_path, nolock=nolock),
            poolclass=NullPool,
        )
        _configure_engine(engine, network=network or nolock)
        return engine

    engine = _engine(nolock=False)
    try:
        Base.metadata.create_all(engine)
        _migrate_schema(engine)
        return engine
    except Exception:
        engine.dispose()
        if not network:
            raise
        engine = _engine(nolock=True)
        Base.metadata.create_all(engine)
        _migrate_schema(engine)
        return engine


def get_session(request: Request) -> Generator[Session, None, None]:
    factory: sessionmaker[Session] = request.app.state.session_factory
    session = factory()
    try:
        yield session
        if session.is_active:
            session.commit()
    except Exception:
        if session.is_active:
            session.rollback()
        raise
    finally:
        session.close()


def sqlite_locked(exc: BaseException) -> bool:
    """True when SQLite rejected a writer (busy / database is locked)."""
    cur: BaseException | None = exc
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, sqlite3.OperationalError):
            msg = str(cur).lower()
            return "locked" in msg or "busy" in msg
        orig = getattr(cur, "orig", None)
        nxt: BaseException | None
        if isinstance(orig, BaseException):
            nxt = orig
        else:
            nxt = cur.__cause__ if cur.__cause__ is not cur else None
        cur = nxt
    return "database is locked" in str(exc).lower()
