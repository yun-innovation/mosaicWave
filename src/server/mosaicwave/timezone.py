"""The display time zone (doc/i18n.md § Time zone).

Every time the server stores or sends in a timestamp field is UTC (ISO 8601). A user may choose the
zone times are *shown* in; it is saved as ``user.time_zone``, an IANA name such as ``Asia/Taipei``,
or NULL for automatic (the device's own zone). The server only validates and stores the name; the
web formats with it, and any date the API writes inside a sentence uses it too.
"""

from __future__ import annotations

from functools import lru_cache
from zoneinfo import ZoneInfo, available_timezones


@lru_cache(maxsize=1)
def _by_lower_name() -> dict[str, str]:
    return {name.lower(): name for name in available_timezones()}


def canonical_zone(name: str | None) -> str | None:
    """The IANA name as the database spells it (``asia/taipei`` → ``Asia/Taipei``), or None if unknown."""
    if not name:
        return None
    found = _by_lower_name().get(name.strip().lower())
    if found is None:
        return None
    try:
        ZoneInfo(found)
    except (OSError, ValueError, KeyError):
        return None
    return found
