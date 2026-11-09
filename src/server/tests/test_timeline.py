"""Timeline scrubber API: GET /assets/months and GET /assets?before= (doc/web-ui.md § Timeline scrubber)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import update

from authutil import authed_client, library_dir, library_session, scan_library
from mosaicwave.config import Settings
from mosaicwave.models import Asset

# name -> when it was taken (the file's mtime is the fallback taken_at)
PHOTOS = {
    "mar_late.jpg": datetime(2024, 3, 30, 22, 0, tzinfo=timezone.utc),
    "mar_early.jpg": datetime(2024, 3, 2, 8, 0, tzinfo=timezone.utc),
    "dec.jpg": datetime(2023, 12, 31, 23, 30, tzinfo=timezone.utc),
    "jun.jpg": datetime(2022, 6, 15, 12, 0, tzinfo=timezone.utc),
}


def _library(tmp_path: Path, photos: dict[str, datetime] = PHOTOS) -> TestClient:
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    client = authed_client(settings)
    folder = library_dir(settings, client)
    for index, (name, when) in enumerate(photos.items()):
        buf = BytesIO()
        Image.new("RGB", (16, 12), (index * 40, 90, 160)).save(buf, format="JPEG")
        path = folder / name
        path.write_bytes(buf.getvalue())
        os.utime(path, (when.timestamp(), when.timestamp()))
    scan_library(client)
    return client


def _names(client: TestClient, **params: str) -> list[str]:
    res = client.get("/api/v1/assets", params={"limit": 50, **params})
    assert res.status_code == 200, res.text
    return [row["filename"] for row in res.json()["items"]]


def test_months_are_counted_newest_first(tmp_path: Path) -> None:
    client = _library(tmp_path)
    body = client.get("/api/v1/assets/months").json()
    assert body == {
        "months": [
            {"month": "2024-03", "count": 2},
            {"month": "2023-12", "count": 1},
            {"month": "2022-06", "count": 1},
        ],
        "undated": 0,
    }


def test_undated_photos_are_counted_apart(tmp_path: Path) -> None:
    client = _library(tmp_path)
    with library_session(client) as session:
        session.execute(update(Asset).where(Asset.relative_path == "jun.jpg").values(taken_at=None))
        session.commit()
    body = client.get("/api/v1/assets/months").json()
    assert [m["month"] for m in body["months"]] == ["2024-03", "2023-12"]
    assert body["undated"] == 1


def test_before_starts_the_list_at_a_point_in_time(tmp_path: Path) -> None:
    client = _library(tmp_path)
    assert _names(client) == ["mar_late.jpg", "mar_early.jpg", "dec.jpg", "jun.jpg"]
    # Jump to December 2023: everything taken before the start of January 2024.
    assert _names(client, before="2024-01-01T00:00:00Z") == ["dec.jpg", "jun.jpg"]
    # An offset instant means the same thing as its UTC equivalent.
    assert _names(client, before="2024-01-01T08:00:00+08:00") == ["dec.jpg", "jun.jpg"]
    assert _names(client, before="2022-01-01T00:00:00Z") == []


def test_before_still_pages_and_keeps_undated_last(tmp_path: Path) -> None:
    client = _library(tmp_path)
    with library_session(client) as session:
        session.execute(update(Asset).where(Asset.relative_path == "jun.jpg").values(taken_at=None))
        session.commit()
    first = client.get("/api/v1/assets", params={"limit": 1, "before": "2024-01-01T00:00:00Z"}).json()
    assert [row["filename"] for row in first["items"]] == ["dec.jpg"]
    rest = client.get("/api/v1/assets", params={"limit": 5, "cursor": first["next_cursor"]}).json()
    assert [row["filename"] for row in rest["items"]] == ["jun.jpg"]  # undated, after every month
    # A date far in the past reaches the undated photos only.
    assert _names(client, before="0001-01-01T00:00:00Z") == ["jun.jpg"]


def test_months_follow_an_album(tmp_path: Path) -> None:
    client = _library(tmp_path)
    album = client.post("/api/v1/albums", json={"name": "Winter"}).json()
    dec = next(row for row in client.get("/api/v1/assets").json()["items"] if row["filename"] == "dec.jpg")
    added = client.post(f"/api/v1/albums/{album['id']}/assets", json={"asset_ids": [dec["id"]], "base_rev": album["rev"]})
    assert added.status_code == 200, added.text
    body = client.get("/api/v1/assets/months", params={"album_id": album["id"]}).json()
    assert body["months"] == [{"month": "2023-12", "count": 1}]
    assert client.get("/api/v1/assets/months", params={"album_id": "nope"}).status_code == 404


def _months(client: TestClient, **params: object) -> dict[str, int]:
    res = client.get("/api/v1/assets/months", params=params)
    assert res.status_code == 200, res.text
    return {row["month"]: row["count"] for row in res.json()["months"]}


def test_months_follow_the_zone_in_the_request(tmp_path: Path) -> None:
    client = _library(tmp_path)
    # dec.jpg is 2023-12-31 23:30 UTC: already January in Taipei (+08:00), still December in Los Angeles.
    assert _months(client) == {"2024-03": 2, "2023-12": 1, "2022-06": 1}
    assert _months(client, time_zone="Asia/Taipei") == {"2024-03": 2, "2024-01": 1, "2022-06": 1}
    assert _months(client, time_zone="america/los_angeles") == {"2024-03": 2, "2023-12": 1, "2022-06": 1}
    assert client.get("/api/v1/assets/months", params={"time_zone": "Mars/Base"}).status_code == 400


def test_months_default_to_the_accounts_saved_zone(tmp_path: Path) -> None:
    client = _library(tmp_path)
    assert client.patch("/api/v1/me", json={"time_zone": "Asia/Taipei"}).status_code == 200
    assert _months(client) == {"2024-03": 2, "2024-01": 1, "2022-06": 1}
    assert _months(client, time_zone="UTC") == {"2024-03": 2, "2023-12": 1, "2022-06": 1}  # the request wins


def test_months_hold_standard_time_until_summer_time_is_on(tmp_path: Path) -> None:
    # 2023-09-30 22:30 UTC: 23:30 on Sep 30 in Amsterdam's standard time (+01:00), but 00:30 on
    # Oct 1 in its summer time (+02:00).
    client = _library(tmp_path, {"edge.jpg": datetime(2023, 9, 30, 22, 30, tzinfo=timezone.utc)})
    assert _months(client, time_zone="Europe/Amsterdam") == {"2023-09": 1}
    assert _months(client, time_zone="Europe/Amsterdam", use_dst="true") == {"2023-10": 1}
    client.patch("/api/v1/me", json={"time_zone": "Europe/Amsterdam", "use_dst": True})
    assert _months(client) == {"2023-10": 1}  # the saved switch is the default
    # A zone with no summer time ignores the switch.
    assert _months(client, time_zone="Asia/Taipei", use_dst="true") == {"2023-10": 1}
