"""Maintenance mode: a flag file beside the site database.

While the flag exists, only admins can use the API (everyone else gets ``503 maintenance``). The
file survives a restart, and the command line works without the API running:

    python -m mosaicwave.maintenance on [--reason TEXT]
    python -m mosaicwave.maintenance off
    python -m mosaicwave.maintenance status

State is derived, never stored: no flag is ``off``; a flag while a job runs is ``draining`` (the
job finishes, new jobs are refused because one is already running); a flag with no running job is
``on``.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from mosaicwave.config import Settings

FLAG_NAME = "maintenance.json"
ENV_START = "MOSAICWAVE_MAINTENANCE"
DEFAULT_START_REASON = "Started in maintenance mode"
MAX_REASON = 500

OFF = "off"
DRAINING = "draining"
ON = "on"


@dataclass(frozen=True)
class Flag:
    reason: str
    since: str
    by: str


def flag_path(settings: Settings) -> Path:
    """Beside the site database, the same folder backups and offline restore use."""
    return settings.database_path.parent / FLAG_NAME


def clean_reason(reason: str | None) -> str:
    return (reason or "").strip()[:MAX_REASON]


def read(settings: Settings) -> Flag | None:
    """The flag, or None when maintenance is off. An unreadable file still means maintenance."""
    path = flag_path(settings)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return Flag("", "", "")
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except ValueError:
        return Flag("", "", "")
    return Flag(
        reason=clean_reason(str(data.get("reason", ""))),
        since=str(data.get("since", "")),
        by=str(data.get("by", "")),
    )


def enable(settings: Settings, reason: str | None = None, by: str = "") -> Flag:
    """Switch maintenance on. Already on: the reason is replaced and ``since`` is kept."""
    current = read(settings)
    since = current.since if current is not None and current.since else datetime.now(timezone.utc).isoformat()
    flag = Flag(reason=clean_reason(reason), since=since, by=by)
    path = flag_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps({"reason": flag.reason, "since": flag.since, "by": flag.by}, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return flag


def disable(settings: Settings) -> bool:
    """Switch maintenance off. True when it was on."""
    path = flag_path(settings)
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed


def state_of(flag: Flag | None, job_running: bool) -> str:
    if flag is None:
        return OFF
    return DRAINING if job_running else ON


def start_from_env(settings: Settings, environ: Mapping[str, str] | None = None) -> bool:
    """``MOSAICWAVE_MAINTENANCE=1`` starts the API in maintenance mode. True when it switched it on."""
    env = os.environ if environ is None else environ
    if (env.get(ENV_START) or "").strip().lower() not in {"1", "true", "yes", "on"}:
        return False
    if read(settings) is not None:
        return False
    enable(settings, DEFAULT_START_REASON, by="env")
    return True


def job_running_in_file(settings: Settings) -> bool:
    """Command-line check of the site database (the API uses its own session)."""
    path = settings.database_path
    if not path.is_file():
        return False
    try:
        con = sqlite3.connect(os.fspath(path.resolve()), timeout=5.0)
    except sqlite3.Error:
        return False
    try:
        return con.execute("SELECT 1 FROM job WHERE status = 'running' LIMIT 1").fetchone() is not None
    except sqlite3.Error:
        return False
    finally:
        con.close()


def _describe(settings: Settings) -> str:
    flag = read(settings)
    state = state_of(flag, job_running_in_file(settings) if flag is not None else False)
    if flag is None:
        return "Maintenance mode is off."
    lines = [f"Maintenance mode is {state}."]
    if flag.since:
        lines.append(f"Since: {flag.since}")
    if flag.reason:
        lines.append(f"Reason: {flag.reason}")
    if state == DRAINING:
        lines.append("A job is still running; it will finish first.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m mosaicwave.maintenance",
        description="Switch maintenance mode on or off. Works with or without the API running.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    on = sub.add_parser("on", help="switch maintenance mode on")
    on.add_argument("--reason", default="", help="shown to users (up to %d characters)" % MAX_REASON)
    sub.add_parser("off", help="switch maintenance mode off")
    sub.add_parser("status", help="show the current state")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    if args.command == "on":
        enable(settings, args.reason, by="cli")
    elif args.command == "off":
        disable(settings)
    print(_describe(settings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
