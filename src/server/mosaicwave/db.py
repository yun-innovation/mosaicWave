from __future__ import annotations

import os
import sqlite3
from collections.abc import Generator
from pathlib import Path

from fastapi import Request
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from mosaicwave import schema
from mosaicwave.config import Settings, ensure_data_dir_exists
from mosaicwave.models import Base


def _refuse_newer_schema(path: Path, *, nolock: bool) -> None:
    """Read user_version without the engine's pragmas, so a too-new file is never written."""
    if not path.is_file():
        return
    if nolock:
        con = sqlite3.connect(_sqlite_file_uri(path, "mode=ro&nolock=1"), uri=True, timeout=30.0)
    else:
        con = sqlite3.connect(os.fspath(path.resolve()), timeout=30.0)
    try:
        found = schema.stored_version(con)
    finally:
        con.close()
    if found > schema.CURRENT_VERSION:
        raise schema.SchemaTooNew(path, found, schema.CURRENT_VERSION)


def _ensure_schema(engine: Engine, path: Path) -> int:
    raw = engine.raw_connection()
    try:
        return schema.ensure_schema(
            raw.driver_connection, path, lambda: Base.metadata.create_all(engine)
        )
    finally:
        raw.close()


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

    try:
        _refuse_newer_schema(db_path, nolock=False)
    except sqlite3.OperationalError:
        if not network:
            raise
        _refuse_newer_schema(db_path, nolock=True)

    engine = _engine(nolock=False)
    try:
        _ensure_schema(engine, db_path)
        return engine
    except schema.SchemaTooNew:
        engine.dispose()
        raise
    except Exception:
        engine.dispose()
        if not network:
            raise
        engine = _engine(nolock=True)
        _ensure_schema(engine, db_path)
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
