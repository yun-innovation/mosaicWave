"""Every time the server stores, sends or writes is UTC, whatever zone the host runs in (doc/i18n.md § Time zone).

Covers the API fields, a Takeout import, backup and restore, and export. Where the platform allows it the
host is moved to a zone far from UTC first, so any use of local time would show up as a shifted value.
"""

from __future__ import annotations

import json
import os
import re
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from authutil import authed_client, library_dir, scan_library
from mosaicwave.config import Settings
from mosaicwave.ziptime import entry_mtime, ut_extra, zip_time
from test_cancel import _until
from test_takeout import _jpeg_bytes, _wait_import, _write_takeout

SIDECAR_EPOCH = 1700000000  # what _write_takeout puts in photoTakenTime: 2023-11-14 22:13:20 UTC
SIDECAR_ISO = "2023-11-14T22:13:20Z"
OFFSET = re.compile(r"(Z|[+-]\d\d:\d\d)$")
TIME_KEYS = re.compile(r"(^|_)(at|since)$")


@pytest.fixture(autouse=True)
def far_from_utc(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Run the host in Auckland (UTC+12/13) where the platform lets a process change its zone."""
    if not hasattr(time, "tzset"):
        yield  # Windows: no tzset; CI on Linux runs these tests in the foreign zone
        return
    monkeypatch.setenv("TZ", "Pacific/Auckland")
    time.tzset()
    yield
    monkeypatch.delenv("TZ")
    time.tzset()


def _settings(tmp_path: Path, takeout: Path | None = None) -> Settings:
    return Settings(data_dir=tmp_path / "data", takeout_dir=takeout, source_dir=None)


def _stamps(value: Any, path: str = "") -> list[tuple[str, str]]:
    """Every string under a key such as created_at, at, since: the places the API puts a time."""
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str) and TIME_KEYS.search(key):
                found.append((f"{path}.{key}", item))
            else:
                found += _stamps(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found += _stamps(item, f"{path}[{index}]")
    return found


def _check(where: str, body: Any, seen: list[str]) -> None:
    for path, text in _stamps(body, where):
        if not text:
            continue  # an empty string means "no time yet" (a job event with none)
        assert OFFSET.search(text), f"{path} = {text!r} has no UTC offset"
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        assert parsed.utcoffset() is not None and parsed.utcoffset().total_seconds() == 0, f"{path} = {text!r} is not UTC"
        seen.append(path)


def test_every_time_the_api_sends_is_utc_with_an_offset(tmp_path: Path) -> None:
    takeout = tmp_path / "Takeout"
    _write_takeout(takeout, _jpeg_bytes())
    client = authed_client(_settings(tmp_path, takeout))
    job = _wait_import(client, str(takeout))
    asset = client.get("/api/v1/assets").json()["items"][0]
    album = client.get("/api/v1/albums").json()[0]
    backup = client.post("/api/v1/me/backups").json()
    _until(client, backup["id"], {"done"})
    site = client.post("/api/v1/backups").json()  # the admin's whole-site backup
    _until(client, site["id"], {"done"})
    export = client.post("/api/v1/me/exports", json={"all": True}).json()
    _until(client, export["id"], {"done"})

    seen: list[str] = []
    for url in (
        "/api/v1/assets",
        f"/api/v1/assets/{asset['id']}",
        "/api/v1/albums",
        f"/api/v1/albums/{album['id']}",
        "/api/v1/sources",
        "/api/v1/jobs",
        f"/api/v1/jobs/{job['id']}",
        f"/api/v1/jobs/{job['id']}/events",
        "/api/v1/sync/changes?since_rev=0",
        "/api/v1/me/backups",
        "/api/v1/backups",
        "/api/v1/backups/restore",
        "/api/v1/maintenance",
        "/api/v1/me/exports",
        "/api/v1/users",
    ):
        res = client.get(url)
        assert res.status_code == 200, (url, res.text)
        _check(url, res.json(), seen)
    # Not an empty pass: the walk really met timestamps from each place.
    joined = " ".join(seen)
    for expected in ("taken_at", "created_at", "finished_at", "updated_at", "last_scan_at", ".at"):
        assert expected in joined, f"no {expected} field was checked: {seen}"


def test_takeout_import_reads_the_sidecar_as_utc(tmp_path: Path) -> None:
    takeout = tmp_path / "Takeout"
    _write_takeout(takeout, _jpeg_bytes())
    client = authed_client(_settings(tmp_path, takeout))
    _wait_import(client, str(takeout))
    assert client.get("/api/v1/assets").json()["items"][0]["taken_at"] == SIDECAR_ISO


def _library_with_dated_files(tmp_path: Path) -> tuple[Settings, TestClient, dict[str, float]]:
    settings = _settings(tmp_path)
    client = authed_client(settings)
    folder = library_dir(settings, client)
    stamps = {
        "a.jpg": datetime(2022, 6, 15, 12, 30, 40, tzinfo=timezone.utc).timestamp(),
        "b.jpg": datetime(2024, 12, 31, 23, 59, 58, tzinfo=timezone.utc).timestamp(),
    }
    for index, (name, when) in enumerate(stamps.items()):
        path = folder / name
        path.write_bytes(_jpeg_bytes((index * 90, 80, 160)))
        os.utime(path, (when, when))
    scan_library(client)
    return settings, client, stamps


def _taken(client: TestClient) -> dict[str, str]:
    return {row["filename"]: row["taken_at"] for row in client.get("/api/v1/assets").json()["items"]}


def test_library_backup_and_restore_keep_times_exactly(tmp_path: Path) -> None:
    settings, client, stamps = _library_with_dated_files(tmp_path)
    before = _taken(client)
    assert before["a.jpg"].startswith("2022-06-15T12:30:40")  # the file's mtime, read as UTC

    res = client.post("/api/v1/me/backups")
    assert res.status_code == 200, res.text
    assert _until(client, res.json()["id"], {"done", "error"})["status"] == "done"
    backup = client.get("/api/v1/me/backups").json()[0]
    assert OFFSET.search(backup["created_at"]) and backup["created_at"].endswith(("Z", "+00:00"))

    folder = library_dir(settings, client)
    for name in stamps:
        (folder / name).unlink()  # lose the files, then restore them
    res = client.post(f"/api/v1/me/backups/{backup['name']}/restore")
    assert res.status_code == 200, res.text
    assert _until(client, res.json()["id"], {"done", "error"})["status"] == "done"

    assert _taken(client) == before
    for name, when in stamps.items():
        # The zip stores the exact second as an extended timestamp, so no zone and no 2-second rounding.
        assert int((folder / name).stat().st_mtime) == int(when), name


def test_backup_zip_entries_are_utc(tmp_path: Path) -> None:
    settings, client, stamps = _library_with_dated_files(tmp_path)
    res = client.post("/api/v1/me/backups")
    _until(client, res.json()["id"], {"done"})
    name = client.get("/api/v1/me/backups").json()[0]["name"]
    archive = next(settings.data_dir.rglob(name))
    with zipfile.ZipFile(archive) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        assert OFFSET.search(manifest["created_at"])
        info = zf.getinfo(f"libraries/{client.get('/api/v1/me').json()['id']}/b.jpg")
        # DOS time inside the zip is the UTC wall clock (rounded to 2 seconds), not the host's.
        assert info.date_time == (2024, 12, 31, 23, 59, 58)


def test_export_writes_utc_and_a_takeout_export_imports_back_unchanged(tmp_path: Path) -> None:
    takeout = tmp_path / "Takeout"
    _write_takeout(takeout, _jpeg_bytes())
    settings = _settings(tmp_path / "one", takeout)
    client = authed_client(settings)
    _wait_import(client, str(takeout))
    assert client.get("/api/v1/assets").json()["items"][0]["taken_at"] == SIDECAR_ISO

    res = client.post("/api/v1/me/exports", json={"all": True, "layout": "takeout"})
    assert res.status_code == 200, res.text
    assert _until(client, res.json()["id"], {"done", "error"})["status"] == "done"
    view = client.get("/api/v1/me/exports").json()[0]
    assert OFFSET.search(view["created_at"])
    folder = next(settings.export_dir.rglob(view["name"]))

    sidecars: list[dict] = []
    for part in sorted(folder.glob("*.zip")):
        with zipfile.ZipFile(part) as zf:
            for member in zf.infolist():
                if member.filename.endswith(".json") and not member.filename.endswith("manifest.json"):
                    sidecars.append(json.loads(zf.read(member)))
                if member.filename.endswith(".jpg"):
                    assert member.date_time == (2023, 11, 14, 22, 13, 20), member.date_time  # UTC wall clock
                    assert entry_mtime(member) == SIDECAR_EPOCH  # and the exact second, for readers that honour it
    assert sidecars, list(folder.iterdir())
    stamp = sidecars[0]["photoTakenTime"]
    assert stamp["timestamp"] == str(SIDECAR_EPOCH)
    assert stamp["formatted"].endswith("UTC") and "10:13:20 PM" in stamp["formatted"]

    # Round trip: import the export into a second, empty server; the time is the same instant.
    other = authed_client(_settings(tmp_path / "two", None))
    _wait_import(other, str(folder))
    assert other.get("/api/v1/assets").json()["items"][0]["taken_at"] == SIDECAR_ISO


def test_zip_time_helpers_round_trip_in_utc() -> None:
    epoch = int(datetime(2021, 3, 4, 5, 6, 7, tzinfo=timezone.utc).timestamp())
    info = zipfile.ZipInfo("x.jpg", zip_time(epoch))
    assert info.date_time == (2021, 3, 4, 5, 6, 7)
    assert entry_mtime(info) == epoch  # no extra field: the zip time read as UTC, not as local
    info.extra = ut_extra(epoch + 1)
    assert entry_mtime(info) == epoch + 1  # the exact field wins over the two-second zip time
    assert zip_time(0) == (1980, 1, 1, 0, 0, 0)  # before a zip can hold: clamped, not wrapped


def test_a_takeout_zip_entry_with_an_exact_time_is_read_by_it(tmp_path: Path) -> None:
    from mosaicwave.storage.takeoutzip import _zip_mtime

    epoch = int(datetime(2022, 8, 9, 10, 11, 13, tzinfo=timezone.utc).timestamp())  # an odd second
    info = zipfile.ZipInfo("Takeout/Google Photos/Photos from 2022/a.jpg", zip_time(epoch))
    info.extra = ut_extra(epoch)
    assert _zip_mtime(info) == epoch
