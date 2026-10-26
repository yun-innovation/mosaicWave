"""Crash safety for Move library (milestone 13, doc/maintenance.md).

``data-dir-move.json`` sits in the app folder. It is written before anything is copied and removed
when the move is over, so a leftover file at start means a crash. ``recover_data_dir_move`` then
looks at where the bootstrap ``config.json`` points to decide, because that file is what makes the
new folder the live one:

* it points at the new folder: the move had finished and verified; the old files still need
  deleting, so finish that;
* it still points at the old folder: the move never took effect; remove the partial copy and, if
  the databases were being rewritten, point their source paths back at the old folder.

Either way the library is whole in one folder afterwards, never split between two.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from mosaicwave.config import (
    Settings,
    _delete_named,
    _rewrite_library_root_uris,
    clear_bootstrap_library_payload,
)

JOURNAL_NAME = "data-dir-move.json"
COPYING = "copying"
REWRITING = "rewriting"

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Journal:
    src: Path
    dest: Path
    names: list[str]
    phase: str


def journal_path(settings: Settings) -> Path:
    return settings.database_path.parent / JOURNAL_NAME


def write_journal(settings: Settings, src: Path, dest: Path, names: list[str], phase: str) -> None:
    path = journal_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps({"src": str(src), "dest": str(dest), "names": names, "phase": phase}, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def read_journal(settings: Settings) -> Journal | None:
    path = journal_path(settings)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Journal(
            src=Path(data["src"]),
            dest=Path(data["dest"]),
            names=[str(n) for n in data["names"]],
            phase=str(data["phase"]),
        )
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError):
        _log.warning("data-dir-move.json is unreadable; leaving the folders alone")
        return None


def clear_journal(settings: Settings) -> None:
    journal_path(settings).unlink(missing_ok=True)


def _same(a: Path, b: Path) -> bool:
    try:
        return a.expanduser().resolve() == b.expanduser().resolve()
    except OSError:
        return False


def recover_data_dir_move(settings: Settings) -> str | None:
    """Undo or finish a move a crash cut short. Returns ``finished``, ``rolled_back`` or None.

    Runs at start, before any database is opened. If a step fails it logs and keeps the journal, so the
    next start tries again. A journal that matches neither folder is left alone for a person to look at.
    """
    journal = read_journal(settings)
    if journal is None:
        return None
    boot = settings.database_path.parent
    try:
        if _same(settings.data_dir, journal.dest):
            _delete_named(journal.src, journal.names)
            clear_bootstrap_library_payload(boot, journal.dest)
            outcome = "finished"
        elif _same(settings.data_dir, journal.src):
            if journal.phase == REWRITING and settings.database_path.is_file():
                _rewrite_library_root_uris(settings.database_path, journal.dest, journal.src)
            _delete_named(journal.dest, journal.names)
            outcome = "rolled_back"
        else:
            _log.warning(
                "data-dir-move.json points at %s and %s but the data folder is %s; not touching anything",
                journal.src,
                journal.dest,
                settings.data_dir,
            )
            return None
    except Exception:
        _log.exception("could not finish recovering an interrupted Move library; will retry at next start")
        return None
    clear_journal(settings)
    _log.warning("an interrupted Move library was %s", "finished" if outcome == "finished" else "rolled back")
    return outcome
