"""Split the single library.db into a site database and one library database per user.

Milestone 6, doc/per-library-db.md. The site database keeps `user`, `local_credential`, `job`
and `job_event`; each user's `source`, `asset`, `album` and `album_asset` rows move to
accounts/{user_id}/library.db. `source_grant` is dropped (share links replace it).

The site tables stay authoritative until the final drop commits, so every earlier stage is
re-runnable: library files are built beside their final name, verified, then renamed into place,
and an interrupted run is simply redone. Nothing here is wired into startup yet.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from mosaicwave import schema
from mosaicwave.schema.library import LIBRARY_CURRENT_VERSION, create_library_schema
from mosaicwave.schema.steps import STEPS, Step

_log = logging.getLogger(__name__)

SPLIT_VERSION = 3
REPORT_NAME = "library.db.split-report.json"

_COPY = ("source", "asset", "album", "album_asset")


def _drop_moved_tables(con: sqlite3.Connection) -> None:
    for table in ("album_asset", "album", "asset", "source_grant", "source"):
        con.execute(f'DROP TABLE IF EXISTS "{table}"')


SPLIT_STEP = Step(
    SPLIT_VERSION,
    "per-library databases: source, asset, album, album_asset moved to accounts/{id}/library.db; "
    "source_grant dropped",
    _drop_moved_tables,
    foreign_keys_off=True,
)


class SplitError(RuntimeError):
    pass


@dataclass
class UserCounts:
    sources: int = 0
    assets: int = 0
    albums: int = 0
    album_assets: int = 0


@dataclass
class SplitReport:
    from_version: int
    users: dict[str, UserCounts] = field(default_factory=dict)
    orphan_sources: int = 0
    orphan_assets: int = 0
    orphan_albums: int = 0
    orphan_album_assets: int = 0
    cross_library_album_assets: int = 0
    covers_cleared: int = 0
    grants_dropped: int = 0

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @property
    def skipped(self) -> int:
        return (
            self.orphan_sources
            + self.orphan_assets
            + self.orphan_albums
            + self.orphan_album_assets
            + self.cross_library_album_assets
        )


def _columns(con: sqlite3.Connection, table: str, schema_name: str = "main") -> list[str]:
    return [row[1] for row in con.execute(f'PRAGMA {schema_name}.table_info("{table}")')]


def _count(con: sqlite3.Connection, sql: str, *args: object) -> int:
    return int(con.execute(sql, args).fetchone()[0])


def _cols(con: sqlite3.Connection, table: str, *, exclude: tuple[str, ...] = ()) -> str:
    return ", ".join(f'"{c}"' for c in _columns(con, table) if c not in exclude)


def _build_library(site: Path, user_id: str, target: Path) -> UserCounts:
    """Create `target` (a library database) holding `user_id`'s rows from the site database."""
    target.unlink(missing_ok=True)
    con = sqlite3.connect(os.fspath(target), isolation_level=None)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        create_library_schema(con)
        con.execute("ATTACH DATABASE ? AS site", (os.fspath(site),))
        con.execute("BEGIN IMMEDIATE")
        try:
            for table in ("source", "asset", "album", "album_asset"):
                have = set(_columns(con, table, "site"))
                missing = set(_columns(con, table)) - have
                if missing:
                    raise SplitError(f"site table {table} lacks columns {sorted(missing)}")
            cols = {t: _cols(con, t) for t in _COPY}
            con.execute(
                f"INSERT INTO main.source ({cols['source']}) "
                f"SELECT {cols['source']} FROM site.source WHERE owner_user_id = ?",
                (user_id,),
            )
            con.execute(
                f"INSERT INTO main.asset ({cols['asset']}) SELECT {cols['asset']} FROM site.asset "
                "WHERE source_id IN (SELECT id FROM main.source)"
            )
            album_cols = _cols(con, "album", exclude=("cover_asset_id",))
            con.execute(
                f"INSERT INTO main.album ({album_cols}, cover_asset_id) "
                f"SELECT {album_cols}, CASE WHEN cover_asset_id IN (SELECT id FROM main.asset) "
                "THEN cover_asset_id ELSE NULL END FROM site.album "
                "WHERE source_id IN (SELECT id FROM main.source)"
            )
            con.execute(
                f"INSERT INTO main.album_asset ({cols['album_asset']}) "
                f"SELECT {cols['album_asset']} FROM site.album_asset "
                "WHERE album_id IN (SELECT id FROM main.album) "
                "AND asset_id IN (SELECT id FROM main.asset)"
            )
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        counts = UserCounts(
            sources=_count(con, "SELECT count(*) FROM main.source"),
            assets=_count(con, "SELECT count(*) FROM main.asset"),
            albums=_count(con, "SELECT count(*) FROM main.album"),
            album_assets=_count(con, "SELECT count(*) FROM main.album_asset"),
        )
        if con.execute("PRAGMA main.foreign_key_check").fetchall():
            raise SplitError(f"library for {user_id} has broken foreign keys")
        if con.execute("PRAGMA main.integrity_check").fetchone()[0] != "ok":
            raise SplitError(f"library for {user_id} failed the integrity check")
        con.execute("DETACH DATABASE site")
        schema.record_version(con, LIBRARY_CURRENT_VERSION, "created (split from the site database)")
        con.execute("PRAGMA journal_mode=DELETE")
        return counts
    finally:
        con.close()


def split_site_database(site_db: Path, accounts_dir: Path) -> SplitReport:
    """Copy each user's library rows out of a v2 site database. Leaves the site database alone.

    Raises SplitError (and removes everything it built) when counts do not add up.
    """
    site_db = Path(site_db)
    accounts_dir = Path(accounts_dir)
    con = sqlite3.connect(os.fspath(site_db), isolation_level=None)
    try:
        con.execute("PRAGMA query_only=ON")
        report = SplitReport(from_version=schema.stored_version(con))
        owned = "SELECT id FROM source WHERE owner_user_id IN (SELECT id FROM user)"
        owners = [
            row[0]
            for row in con.execute(
                "SELECT DISTINCT owner_user_id FROM source "
                "WHERE owner_user_id IN (SELECT id FROM user) ORDER BY owner_user_id"
            )
        ]
        report.orphan_sources = _count(
            con, "SELECT count(*) FROM source WHERE owner_user_id IS NULL "
            "OR owner_user_id NOT IN (SELECT id FROM user)"
        )
        report.orphan_assets = _count(con, f"SELECT count(*) FROM asset WHERE source_id NOT IN ({owned})")
        report.orphan_albums = _count(
            con, f"SELECT count(*) FROM album WHERE source_id IS NULL OR source_id NOT IN ({owned})"
        )
        report.orphan_album_assets = _count(
            con,
            "SELECT count(*) FROM album_asset WHERE "
            f"album_id IN (SELECT id FROM album WHERE source_id IS NULL OR source_id NOT IN ({owned})) "
            f"OR asset_id IN (SELECT id FROM asset WHERE source_id NOT IN ({owned}))",
        )
        # Rows tying an album to an asset of another library: they cannot exist in one file.
        report.cross_library_album_assets = _count(
            con,
            "SELECT count(*) FROM album_asset aa "
            "JOIN album al ON al.id = aa.album_id JOIN asset a ON a.id = aa.asset_id "
            f"JOIN source s1 ON s1.id = al.source_id JOIN source s2 ON s2.id = a.source_id "
            f"WHERE s1.id IN ({owned}) AND s2.id IN ({owned}) AND s1.owner_user_id != s2.owner_user_id",
        )
        report.covers_cleared = _count(
            con,
            "SELECT count(*) FROM album al JOIN asset a ON a.id = al.cover_asset_id "
            "JOIN source s ON s.id = a.source_id WHERE al.source_id IN "
            f"({owned}) AND s.owner_user_id != (SELECT owner_user_id FROM source WHERE id = al.source_id)",
        )
        if "source_grant" in schema.library_tables(con):
            report.grants_dropped = _count(con, "SELECT count(*) FROM source_grant WHERE deleted_at IS NULL")
        totals = {
            "asset": _count(con, "SELECT count(*) FROM asset"),
            "album": _count(con, "SELECT count(*) FROM album"),
            "album_asset": _count(con, "SELECT count(*) FROM album_asset"),
            "source": _count(con, "SELECT count(*) FROM source"),
        }
    finally:
        con.close()

    accounts_dir.mkdir(parents=True, exist_ok=True)
    built: list[tuple[Path, Path]] = []
    try:
        for user_id in owners:
            final = accounts_dir / user_id / "library.db"
            tmp = accounts_dir / user_id / "library.db.migrating"
            final.parent.mkdir(parents=True, exist_ok=True)
            report.users[user_id] = _build_library(site_db, user_id, tmp)
            built.append((tmp, final))

        moved = UserCounts()
        for counts in report.users.values():
            moved.sources += counts.sources
            moved.assets += counts.assets
            moved.albums += counts.albums
            moved.album_assets += counts.album_assets
        if moved.sources + report.orphan_sources != totals["source"]:
            raise SplitError("source rows do not add up")
        if moved.assets + report.orphan_assets != totals["asset"]:
            raise SplitError("asset rows do not add up")
        if moved.albums + report.orphan_albums != totals["album"]:
            raise SplitError("album rows do not add up")
        if (
            moved.album_assets + report.orphan_album_assets + report.cross_library_album_assets
            != totals["album_asset"]
        ):
            raise SplitError("album_asset rows do not add up")

        for tmp, final in built:
            for sidecar in ("-wal", "-shm", "-journal"):
                Path(os.fspath(final) + sidecar).unlink(missing_ok=True)
            os.replace(tmp, final)
    except BaseException:
        for tmp, final in built:
            tmp.unlink(missing_ok=True)
        for user_id in report.users:
            (accounts_dir / user_id / "library.db.migrating").unlink(missing_ok=True)
        raise
    return report


def migrate_site_database(site_db: Path, accounts_dir: Path) -> SplitReport | None:
    """Bring `site_db` to the split layout. Returns None when there is nothing to do.

    Fresh installs (no file, or an empty one) and databases already at v3 are left alone.
    Otherwise: bring the file to v2, copy each user's library out, then drop the moved tables
    as step 3 (which makes the usual `library.db.pre-v2` copy first).
    """
    site_db = Path(site_db)
    if not site_db.is_file():
        return None
    con = sqlite3.connect(os.fspath(site_db), timeout=30.0)
    try:
        found = schema.stored_version(con)
        if found >= SPLIT_VERSION:
            if found > SPLIT_VERSION:
                raise schema.SchemaTooNew(site_db, found, SPLIT_VERSION)
            return None
        if found == 0 and not schema.library_tables(con):
            return None
    finally:
        con.close()

    con = sqlite3.connect(os.fspath(site_db), timeout=30.0)
    try:
        schema.ensure_schema(con, site_db, lambda: None, STEPS)
    finally:
        con.close()

    report = split_site_database(site_db, accounts_dir)

    con = sqlite3.connect(os.fspath(site_db), timeout=30.0)
    try:
        schema.ensure_schema(con, site_db, lambda: None, [*STEPS, SPLIT_STEP])
    finally:
        con.close()

    note = site_db.with_name(REPORT_NAME)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    note.write_text(f"{report.to_json()}\n", encoding="utf-8")
    _log.info(
        "%s: split into %d library databases at %s; %d rows skipped (details in %s)",
        site_db.name,
        len(report.users),
        stamp,
        report.skipped,
        note.name,
    )
    return report
