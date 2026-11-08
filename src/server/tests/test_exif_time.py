"""EXIF capture time: exact when the camera recorded its UTC offset, the wall clock read as UTC when not."""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from authutil import authed_client, library_dir, scan_library
from mosaicwave.config import Settings
from mosaicwave.library.media import taken_at_from_exif_tags

EXIF_IFD = 0x8769


def _exif(
    original: str | None = None,
    offset: str | None = None,
    *,
    digitized: str | None = None,
    offset_digitized: str | None = None,
    plain: str | None = None,
    offset_plain: str | None = None,
) -> Image.Exif:
    """An EXIF block shaped like a camera's: capture times in the Exif sub-IFD, DateTime at the top."""
    exif = Image.Exif()
    sub = exif.get_ifd(EXIF_IFD)
    if original:
        sub[0x9003] = original
    if offset:
        sub[0x9011] = offset
    if digitized:
        sub[0x9004] = digitized
    if offset_digitized:
        sub[0x9012] = offset_digitized
    if offset_plain:
        sub[0x9010] = offset_plain
    if plain:
        exif[0x0132] = plain
    return exif


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def test_offset_makes_the_time_exact() -> None:
    # 20:30 in Taipei (+08:00) is 12:30 UTC.
    assert taken_at_from_exif_tags(_exif("2024:05:06 20:30:00", "+08:00")) == _utc(2024, 5, 6, 12, 30)
    # 20:30 in New York (-04:00) is 00:30 UTC the next day.
    assert taken_at_from_exif_tags(_exif("2024:05:06 20:30:00", "-04:00")) == _utc(2024, 5, 7, 0, 30)
    assert taken_at_from_exif_tags(_exif("2024:05:06 20:30:00", "+05:30")) == _utc(2024, 5, 6, 15, 0)


def test_no_offset_reads_the_wall_clock_as_utc() -> None:
    assert taken_at_from_exif_tags(_exif("2024:05:06 20:30:00")) == _utc(2024, 5, 6, 20, 30)


@pytest.mark.parametrize("bad", ["", "  ", "UTC", "+8", "+25:00", "08:00", "+08:00:00", "garbage"])
def test_an_unusable_offset_is_ignored_not_guessed(bad: str) -> None:
    assert taken_at_from_exif_tags(_exif("2024:05:06 20:30:00", bad)) == _utc(2024, 5, 6, 20, 30)


def test_other_capture_tags_are_the_fallback_in_order() -> None:
    # No DateTimeOriginal: the digitized time and its own offset.
    got = taken_at_from_exif_tags(_exif(digitized="2024:05:06 20:30:00", offset_digitized="+02:00"))
    assert got == _utc(2024, 5, 6, 18, 30)
    # Only the plain DateTime, with OffsetTime.
    got = taken_at_from_exif_tags(_exif(plain="2024:05:06 20:30:00", offset_plain="+01:00"))
    assert got == _utc(2024, 5, 6, 19, 30)
    # An offset belongs to its own time: DateTimeOriginal does not borrow OffsetTimeDigitized.
    got = taken_at_from_exif_tags(_exif("2024:05:06 20:30:00", digitized="2024:05:06 20:30:00", offset_digitized="+08:00"))
    assert got == _utc(2024, 5, 6, 20, 30)


def test_a_blank_date_falls_through_to_the_next_tag() -> None:
    got = taken_at_from_exif_tags(_exif("0000:00:00 00:00:00", plain="2023:01:02 03:04:05"))
    assert got == _utc(2023, 1, 2, 3, 4, 5)
    assert taken_at_from_exif_tags(_exif("0000:00:00 00:00:00")) is None
    assert taken_at_from_exif_tags(Image.Exif()) is None


def test_a_scanned_photo_gets_the_exact_instant(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    client = authed_client(settings)
    folder = library_dir(settings, client)
    for name, offset in (("taipei.jpg", "+08:00"), ("floating.jpg", None)):
        buf = BytesIO()
        Image.new("RGB", (16, 12), (10, 120, 200)).save(
            buf, format="JPEG", exif=_exif("2024:05:06 20:30:00", offset)
        )
        (folder / name).write_bytes(buf.getvalue())
    scan_library(client)
    taken = {row["filename"]: row["taken_at"] for row in client.get("/api/v1/assets").json()["items"]}
    assert taken["taipei.jpg"] == "2024-05-06T12:30:00Z"
    assert taken["floating.jpg"] == "2024-05-06T20:30:00Z"


def test_a_rescan_corrects_a_date_taken_from_the_file_time(tmp_path: Path) -> None:
    """Libraries scanned before offsets and the Exif sub-IFD were read hold the file's mtime; a rescan fixes them."""
    from sqlalchemy import update

    from authutil import library_session
    from mosaicwave.models import Asset

    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    client = authed_client(settings)
    buf = BytesIO()
    Image.new("RGB", (16, 12), (10, 120, 200)).save(buf, format="JPEG", exif=_exif("2024:05:06 20:30:00", "+08:00"))
    (library_dir(settings, client) / "a.jpg").write_bytes(buf.getvalue())
    scan_library(client)
    with library_session(client) as session:
        session.execute(update(Asset).values(taken_at=datetime(2020, 1, 1)))  # what the old reader stored
        session.commit()
    assert client.get("/api/v1/assets").json()["items"][0]["taken_at"].startswith("2020-01-01")
    scan_library(client)
    assert client.get("/api/v1/assets").json()["items"][0]["taken_at"] == "2024-05-06T12:30:00Z"
