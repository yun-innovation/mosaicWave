"""The display time zone (doc/i18n.md § Time zone).

Every time the server stores or sends in a timestamp field is UTC (ISO 8601). A user may choose the
zone times are *shown* in; it is saved as ``user.time_zone``, an IANA name such as ``Asia/Taipei``,
or NULL for automatic (the device's own zone). The server validates and stores the name; the web
formats with it, and any date the API writes inside a sentence uses it too.

The zone data comes from the ``tzdata`` package only, never from the operating system. mosaicWave
runs on Windows (no zone files), macOS, Linux and a QNAP NAS (a minimal system, often old or
without zone files), so the OS would give a different list and different rules on each. With
``tzdata`` the list, the rules and ``GET /time-zones`` are the same everywhere, and an upgrade of the
package is the one way they change. ``zoneinfo`` is told to look nowhere else when this module loads.
"""

from __future__ import annotations

import importlib.resources
import zoneinfo
from functools import lru_cache

# An empty search path makes ZoneInfo read the tzdata package, and nothing on disk. Only this app
# uses zoneinfo, so setting it for the process is safe.
zoneinfo.reset_tzpath(to=[])

# The areas IANA files real places under. The rest of the database is legacy aliases (US/Pacific,
# Brazil/East, Etc/GMT+8, EST5EDT, GB, ...): still valid when sent, but not offered in a picker.
_AREAS = ("Africa", "America", "Antarctica", "Arctic", "Asia", "Atlantic", "Australia", "Europe", "Indian", "Pacific")


@lru_cache(maxsize=1)
def _names() -> tuple[str, ...]:
    """Every zone name in the installed ``tzdata`` (empty when the package is missing)."""
    try:
        text = importlib.resources.files("tzdata").joinpath("zones").read_text(encoding="utf-8")
    except (ModuleNotFoundError, FileNotFoundError, OSError):
        return ()
    return tuple(sorted(line.strip() for line in text.splitlines() if line.strip()))


@lru_cache(maxsize=1)
def _by_lower_name() -> dict[str, str]:
    return {name.lower(): name for name in _names()}


def zone_data_available() -> bool:
    """False when the ``tzdata`` package is not installed."""
    return bool(_names())


def tzdata_version() -> str | None:
    try:
        import tzdata
    except ModuleNotFoundError:
        return None
    return str(getattr(tzdata, "IANA_VERSION", "")) or None


def zone_choices() -> list[str]:
    """The names a picker offers: ``UTC`` first, then every real place, A to Z."""
    places = [name for name in _names() if name.split("/", 1)[0] in _AREAS and "/" in name]
    return ["UTC", *places]


def canonical_zone(name: str | None) -> str | None:
    """The IANA name as ``tzdata`` spells it (``asia/taipei`` → ``Asia/Taipei``), or None if unknown."""
    if not name:
        return None
    found = _by_lower_name().get(name.strip().lower())
    if found is None:
        return None
    try:
        zoneinfo.ZoneInfo(found)
    except (OSError, ValueError, KeyError):
        return None
    return found
