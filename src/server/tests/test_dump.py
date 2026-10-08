from __future__ import annotations

import zipfile
from pathlib import Path

from mosaicwave.config import Settings
from mosaicwave.library.dump import inspect_takeout_path, parse_oversize_name
from mosaicwave.library.media import is_album_folder, is_date_folder
from authutil import authed_client


def test_date_folders_en_and_zh() -> None:
    assert is_date_folder("Photos from 2024")
    assert is_date_folder("2021相簿")
    assert is_date_folder("2021的相片")
    assert is_date_folder("2021年の写真")
    assert not is_date_folder("Trip to Kyoto")
    assert not is_album_folder("2021的相片")
    assert not is_album_folder("Google 相片")
    assert is_album_folder("DHC SSP training")


def test_parse_oversize_name() -> None:
    orig, part = parse_oversize_name("IMG_3173-040.MOV")
    assert orig == "IMG_3173.MOV"
    assert part == 40
    assert parse_oversize_name("notes.txt") == (None, None)


def _empty_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Takeout/placeholder.txt", "x")


def test_inspect_dump_zip_and_oversized(tmp_path: Path) -> None:
    dump = tmp_path / "download"
    dump.mkdir()
    with zipfile.ZipFile(dump / "takeout-20260101T000000Z-1-001.zip", "w") as zf:
        zf.writestr("Takeout/Google Photos/Photos from 2024/IMG_1.jpg", b"not-a-real-jpeg")
        zf.writestr("Takeout/Google Photos/Photos from 2024/IMG_1.jpg.json", "{}")
        zf.writestr(
            "Takeout/Google Photos/Photos from 2024/holiday.mov.supplemental-metadata.json",
            "{}",
        )
        zf.writestr("Takeout/Google Photos/Trip/metadata.json", '{"title": "Trip"}')
    _empty_zip(dump / "takeout-20260101T000000Z-1-003.zip")
    (dump / "holiday-002.mov").write_bytes(b"fake-video")

    report = inspect_takeout_path(dump)
    assert report.kind == "dump"
    assert report.ready is True
    assert report.photos_root == "Google Photos"
    assert report.import_root == str(dump.resolve())
    assert report.zip_count == 2
    assert report.series[0].missing_parts == [2]
    assert report.album_metadata_count == 1
    assert report.sidecar_without_media_count == 0
    codes = {w.code for w in report.warnings}
    assert "missing_zip_part" not in codes
    assert "reads_zips" in codes
    assert "sidecar_without_media" not in codes
    assert "no_extract" not in codes


def test_inspect_zips_only_not_ready(tmp_path: Path) -> None:
    dump = tmp_path / "download"
    dump.mkdir()
    _empty_zip(dump / "takeout-20260101T000000Z-001.zip")
    report = inspect_takeout_path(dump)
    assert report.ready is False
    assert report.import_root is None
    assert any(w.code == "zip_has_no_photos" for w in report.warnings)


def test_inspect_extracted_takeout(tmp_path: Path) -> None:
    takeout = tmp_path / "Takeout"
    photos = takeout / "Google Photos" / "Photos from 2024"
    photos.mkdir(parents=True)
    (photos / "a.jpg").write_bytes(b"x")
    report = inspect_takeout_path(takeout)
    assert report.kind == "extracted"
    assert report.ready is True
    assert report.zip_count == 0
    assert Path(report.import_root or "").name == "Google Photos"


def test_inspect_uses_selected_folder_not_parent_name(tmp_path: Path) -> None:
    """A parent named 'google photos' is not the dump; zips live in the selected dated folder."""
    container = tmp_path / "google photos"
    dump = container / "20260831T100949Z"
    dump.mkdir(parents=True)
    with zipfile.ZipFile(dump / "takeout-20260831T100949Z-1-001.zip", "w") as zf:
        zf.writestr("Takeout/Google Photos/Photos from 2024/IMG_1.jpg", b"jpeg")
    parent = inspect_takeout_path(container)
    assert parent.ready is False
    assert parent.zip_count == 0
    assert any(w.code == "no_takeout_zips" for w in parent.warnings)
    assert "20260831T100949Z" in " ".join(w.message for w in parent.warnings)
    selected = inspect_takeout_path(dump)
    assert selected.ready is True
    assert selected.zip_count == 1
    assert selected.import_root == str(dump.resolve())


def test_inspect_api(tmp_path: Path) -> None:
    dump = tmp_path / "download"
    dump.mkdir()
    _empty_zip(dump / "takeout-20260101T000000Z-001.zip")
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    client = authed_client(settings)
    res = client.post("/api/v1/import/takeout/inspect", json={"path": str(dump)})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ready"] is False
    assert any(w["code"] == "zip_has_no_photos" for w in body["warnings"])
    blocked = client.post("/api/v1/import/takeout", json={"path": str(dump)})
    assert blocked.status_code == 400
    as_source = client.post("/api/v1/sources", json={"path": str(dump), "kind": "takeout"})
    assert as_source.status_code == 409
