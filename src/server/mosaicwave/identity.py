"""Server identity: which library data this is, which process answered, and when the data was replaced.

Design: doc/mobile.md § Server identity. All three are public values, not secrets.

- ``server_id``: a UUID kept in the site database (``site_setting``). It travels with the database
  through moves and backups. An app keys its saved servers, sessions and caches by it.
- ``data_epoch``: an integer beside it, raised by every restore and every id reset. An app that sees
  it change drops what it cached for that server.
- ``instance_id``: a UUID kept in a file next to ``session.key`` (never in the database, never in an
  archive). It names the running install, for display and diagnostics only.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from mosaicwave.models import SiteSetting, utcnow

SERVER_ID_KEY = "server_id"
DATA_EPOCH_KEY = "data_epoch"
INSTANCE_FILE = "instance.id"


def new_server_id() -> str:
    return str(uuid.uuid4())


def _set(session: Session, key: str, value: str) -> SiteSetting:
    row = session.get(SiteSetting, key)
    if row is None:
        row = SiteSetting(key=key, value=value)
        session.add(row)
    else:
        row.value = value
        row.updated_at = utcnow()
    return row


def _epoch_of(value: str | None) -> int:
    try:
        return max(1, int(value or ""))
    except ValueError:
        return 1


def ensure(session: Session) -> tuple[str, int]:
    """The ``(server_id, data_epoch)`` of this database; either is created when missing."""
    id_row = session.get(SiteSetting, SERVER_ID_KEY)
    epoch_row = session.get(SiteSetting, DATA_EPOCH_KEY)
    if id_row is None or not id_row.value.strip() or epoch_row is None:
        if id_row is None or not id_row.value.strip():
            id_row = _set(session, SERVER_ID_KEY, new_server_id())
        if epoch_row is None:
            epoch_row = _set(session, DATA_EPOCH_KEY, "1")
        session.commit()
    return id_row.value.strip(), _epoch_of(epoch_row.value)


def reset(session: Session) -> tuple[str, int]:
    """Give this database a new ``server_id`` and raise ``data_epoch`` (a copied database, doc/mobile.md)."""
    _, epoch = ensure(session)
    server_id = new_server_id()
    _set(session, SERVER_ID_KEY, server_id)
    _set(session, DATA_EPOCH_KEY, str(epoch + 1))
    session.commit()
    return server_id, epoch + 1


# --- plain sqlite3: upgrade steps and a staged restore (no ORM, no running engine) ---


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def _put(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute(
        "INSERT INTO site_setting (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value, _stamp()),
    )


def read_raw(con: sqlite3.Connection) -> tuple[str | None, int]:
    """``(server_id, data_epoch)`` from a site database; ``(None, 0)`` when it has none (or no table)."""
    try:
        rows = dict(
            con.execute(
                "SELECT key, value FROM site_setting WHERE key IN (?, ?)", (SERVER_ID_KEY, DATA_EPOCH_KEY)
            ).fetchall()
        )
    except sqlite3.OperationalError:
        return None, 0
    server_id = (rows.get(SERVER_ID_KEY) or "").strip() or None
    epoch = _epoch_of(rows.get(DATA_EPOCH_KEY)) if DATA_EPOCH_KEY in rows else 0
    return server_id, epoch


def restamp_staged(staged_db: Path, *, live_epoch: int, keep_id: bool) -> None:
    """Mark a restored site database: raise ``data_epoch`` and decide ``server_id``.

    ``live_epoch`` is the epoch of the database being replaced (0 when there is none). The new epoch is
    above both it and the archive's, so no app that saw either mistakes the restored data for what it
    holds. ``keep_id`` keeps the archive's id; otherwise (the default) a new one is made. An archive from
    before the identity existed has no table or rows to keep: its database gets an id when it is upgraded.
    """
    con = sqlite3.connect(str(staged_db))
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "site_setting" not in tables:
            return
        archive_id, archive_epoch = read_raw(con)
        server_id = archive_id if (keep_id and archive_id) else new_server_id()
        _put(con, SERVER_ID_KEY, server_id)
        _put(con, DATA_EPOCH_KEY, str(max(live_epoch, archive_epoch) + 1))
        con.commit()
    finally:
        con.close()


def live_epoch(db: Path) -> int:
    """``data_epoch`` of the database file at ``db`` (0 when there is none). Read only."""
    if not db.is_file():
        return 0
    try:
        con = sqlite3.connect(str(db.resolve()), timeout=5.0)
    except sqlite3.Error:
        return 0
    try:
        return read_raw(con)[1]
    except sqlite3.Error:
        return 0
    finally:
        con.close()


def instance_id_for(bootstrap: Path) -> str:
    """The id of this install, created on first use beside ``session.key``."""
    path = bootstrap / INSTANCE_FILE
    try:
        stored = path.read_text(encoding="utf-8").strip()
        uuid.UUID(stored)
        return stored
    except (OSError, ValueError):
        pass
    bootstrap.mkdir(parents=True, exist_ok=True)
    created = str(uuid.uuid4())
    path.write_text(created, encoding="utf-8")
    return created
