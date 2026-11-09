"""Times in a zip: always UTC (doc/i18n.md § Time zone).

A zip entry has a time without a zone, and tools disagree on what it means (most say local time).
mosaicWave keeps every time in UTC, in the databases and in the files, so an entry is written with
its modified time in UTC, and exactly: the Info-ZIP "extended timestamp" field holds the UTC epoch
second, and the plain zip time (which has two-second steps) is the same moment in UTC. Reading takes
the exact field when an entry has one, else the plain zip time read as UTC.

Kept free of other mosaicWave imports so storage, backup and export can all use it.
"""

from __future__ import annotations

import struct
import time
import zipfile
from datetime import datetime, timezone

_UT_FIELD = 0x5455
ZIP_MIN = 315532800  # 1980-01-01, the first time a zip entry can hold
ZIP_MAX = 4354819198  # 2107-12-31 23:59:58, the last


def zip_time(epoch: float) -> tuple[int, int, int, int, int, int]:
    """The plain zip time (UTC wall clock, two-second steps) for a UTC epoch second."""
    return time.gmtime(min(max(int(epoch), ZIP_MIN), ZIP_MAX))[:6]  # type: ignore[return-value]


def ut_extra(epoch: float) -> bytes:
    """The extended-timestamp field holding the exact UTC epoch second."""
    return struct.pack("<HHBI", _UT_FIELD, 5, 1, max(int(epoch), 0) & 0xFFFFFFFF)


def entry_mtime(info: zipfile.ZipInfo) -> float:
    """The modified time (UTC epoch) of a zip entry: the exact one if it has it, else its zip time read as UTC."""
    extra, pos = info.extra, 0
    while pos + 4 <= len(extra):
        field, size = struct.unpack_from("<HH", extra, pos)
        if field == _UT_FIELD and size >= 5 and extra[pos + 4] & 1:
            return float(struct.unpack_from("<I", extra, pos + 5)[0])
        pos += 4 + size
    return datetime(*info.date_time, tzinfo=timezone.utc).timestamp()