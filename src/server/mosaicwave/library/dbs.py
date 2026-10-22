"""Per-user library databases: accounts/{user_id}/library.db (milestone 6, doc/per-library-db.md).

Opens a user's file on first use (creating it, or upgrading an older one with a pre-upgrade
copy), keeps a bounded cache of engines, and lists the accounts that have a file. Engines use
NullPool, so a cached engine holds no connection; evicting one is cheap and safe while a
session that already borrowed a connection finishes.
"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from mosaicwave.config import Settings
from mosaicwave.db import open_engine
from mosaicwave.schema.library import LIBRARY_STEPS, create_library_schema

DB_NAME = "library.db"
_USER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _create_current(engine: Engine) -> None:
    raw = engine.raw_connection()
    try:
        con = raw.driver_connection
        con.isolation_level = None
        create_library_schema(con)
    finally:
        raw.close()


class LibraryDbs:
    def __init__(self, settings: Settings, *, max_open: int = 64) -> None:
        if max_open < 1:
            raise ValueError("max_open must be at least 1")
        self._settings = settings
        self._max_open = max_open
        self._lock = threading.Lock()
        self._engines: OrderedDict[str, Engine] = OrderedDict()
        self._factories: dict[str, sessionmaker[Session]] = {}

    def path(self, user_id: str) -> Path:
        if not _USER_ID.match(user_id or ""):
            raise ValueError(f"not a valid user id: {user_id!r}")
        return self._settings.accounts_dir / user_id / DB_NAME

    def exists(self, user_id: str) -> bool:
        return self.path(user_id).is_file()

    def user_ids(self) -> list[str]:
        """Accounts that have a library file, sorted. Does not open them."""
        root = self._settings.accounts_dir
        if not root.is_dir():
            return []
        return sorted(
            p.name for p in root.iterdir() if _USER_ID.match(p.name) and (p / DB_NAME).is_file()
        )

    def engine(self, user_id: str) -> Engine:
        """The engine for `user_id`, creating or upgrading its file on first use."""
        db = self.path(user_id)
        with self._lock:
            found = self._engines.get(user_id)
            if found is not None:
                self._engines.move_to_end(user_id)
                return found
            db.parent.mkdir(parents=True, exist_ok=True)
            engine = open_engine(db, steps=LIBRARY_STEPS, create_current=_create_current)
            self._engines[user_id] = engine
            self._factories[user_id] = sessionmaker(engine, expire_on_commit=False)
            while len(self._engines) > self._max_open:
                old_id, old = self._engines.popitem(last=False)
                self._factories.pop(old_id, None)
                old.dispose()
            return engine

    def factory(self, user_id: str) -> sessionmaker[Session]:
        engine = self.engine(user_id)
        with self._lock:
            found = self._factories.get(user_id)
            if found is None:  # evicted between the two calls
                found = sessionmaker(engine, expire_on_commit=False)
            return found

    def open_count(self) -> int:
        with self._lock:
            return len(self._engines)

    def dispose(self, user_id: str) -> None:
        with self._lock:
            engine = self._engines.pop(user_id, None)
            self._factories.pop(user_id, None)
        if engine is not None:
            engine.dispose()

    def dispose_all(self) -> None:
        with self._lock:
            engines = list(self._engines.values())
            self._engines.clear()
            self._factories.clear()
        for engine in engines:
            engine.dispose()
