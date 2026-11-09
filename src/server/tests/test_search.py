"""Search: GET /assets?q=&taken_from=&taken_to= (doc/web-ui.md § Search)."""

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

# relative path -> when it was taken (the file's mtime is the fallback taken_at)
PHOTOS = {
    "Trip to Kyoto/IMG_0001.jpg": datetime(2024, 3, 30, 22, 0, tzinfo=timezone.utc),
    "Trip to Kyoto/IMG_0002.jpg": datetime(2024, 3, 2, 8, 0, tzinfo=timezone.utc),
    "Home/birthday cake.JPG": datetime(2023, 12, 31, 23, 30, tzinfo=timezone.utc),
    "Home/50%_off.jpg": datetime(2022, 6, 15, 12, 0, tzinfo=timezone.utc),
    "Home/50 percent.jpg": datetime(2022, 6, 14, 12, 0, tzinfo=timezone.utc),
    "旅行/東京.jpg": datetime(2021, 1, 1, 0, 0, tzinfo=timezone.utc),
}


def _library(tmp_path: Path) -> TestClient:
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    client = authed_client(settings)
    folder = library_dir(settings, client)
    for index, (rel, when) in enumerate(PHOTOS.items()):
        buf = BytesIO()
        Image.new("RGB", (16, 12), (index * 30, 90, 160)).save(buf, format="JPEG")
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(buf.getvalue())
        os.utime(path, (when.timestamp(), when.timestamp()))
    scan_library(client)
    return client


def _names(client: TestClient, **params: object) -> list[str]:
    res = client.get("/api/v1/assets", params={"limit": 50, **params})
    assert res.status_code == 200, res.text
    return [row["relative_path"] for row in res.json()["items"]]


def test_name_search_matches_file_and_folder_in_any_case(tmp_path: Path) -> None:
    client = _library(tmp_path)
    assert _names(client, q="BIRTHDAY") == ["Home/birthday cake.JPG"]
    assert _names(client, q="img_000") == ["Trip to Kyoto/IMG_0001.jpg", "Trip to Kyoto/IMG_0002.jpg"]
    assert _names(client, q="kyoto") == ["Trip to Kyoto/IMG_0001.jpg", "Trip to Kyoto/IMG_0002.jpg"]  # the folder
    assert _names(client, q="東京") == ["旅行/東京.jpg"]
    assert _names(client, q="  cake  ") == ["Home/birthday cake.JPG"]  # trimmed
    assert _names(client, q="nothing like this") == []
    assert len(_names(client, q="")) == len(PHOTOS)  # empty means no search


def test_like_wildcards_in_the_search_are_plain_text(tmp_path: Path) -> None:
    client = _library(tmp_path)
    assert _names(client, q="%") == ["Home/50%_off.jpg"]
    assert _names(client, q="_o") == ["Home/50%_off.jpg"]  # "_" is not "any one character"
    assert _names(client, q="50 p") == ["Home/50 percent.jpg"]


def test_date_range_is_from_inclusive_to_exclusive(tmp_path: Path) -> None:
    client = _library(tmp_path)
    got = _names(client, taken_from="2024-03-01T00:00:00Z", taken_to="2024-04-01T00:00:00Z")
    assert got == ["Trip to Kyoto/IMG_0001.jpg", "Trip to Kyoto/IMG_0002.jpg"]
    assert _names(client, taken_from="2024-03-30T22:00:00Z", taken_to="2024-03-30T22:00:01Z") == [
        "Trip to Kyoto/IMG_0001.jpg"
    ]  # the start is included
    assert _names(client, taken_from="2024-03-02T00:00:00Z", taken_to="2024-03-30T22:00:00Z") == [
        "Trip to Kyoto/IMG_0002.jpg"
    ]  # the end is not
    assert _names(client, taken_from="2024-01-01T00:00:00Z") == [
        "Trip to Kyoto/IMG_0001.jpg",
        "Trip to Kyoto/IMG_0002.jpg",
    ]
    # An instant with an offset means the same moment: 2024-01-01 08:00 in Taipei is 00:00 UTC.
    assert _names(client, taken_to="2024-01-01T08:00:00+08:00", taken_from="2023-12-31T00:00:00Z") == [
        "Home/birthday cake.JPG"
    ]


def test_name_and_dates_combine_and_undated_photos_never_match_a_range(tmp_path: Path) -> None:
    client = _library(tmp_path)
    with library_session(client) as session:
        session.execute(update(Asset).where(Asset.relative_path == "Home/50 percent.jpg").values(taken_at=None))
        session.commit()
    assert _names(client, q="home", taken_from="2022-01-01T00:00:00Z", taken_to="2023-01-01T00:00:00Z") == [
        "Home/50%_off.jpg"
    ]
    assert "Home/50 percent.jpg" in _names(client, q="home")  # searching by name alone still finds it
    assert "Home/50 percent.jpg" not in _names(client, taken_from="1900-01-01T00:00:00Z")


def test_filters_hold_on_every_page(tmp_path: Path) -> None:
    client = _library(tmp_path)
    first = client.get("/api/v1/assets", params={"limit": 1, "q": "home"}).json()
    assert [r["relative_path"] for r in first["items"]] == ["Home/birthday cake.JPG"]
    second = client.get("/api/v1/assets", params={"limit": 1, "q": "home", "cursor": first["next_cursor"]}).json()
    assert [r["relative_path"] for r in second["items"]] == ["Home/50%_off.jpg"]
    last = client.get("/api/v1/assets", params={"limit": 5, "q": "home", "cursor": second["next_cursor"]}).json()
    assert [r["relative_path"] for r in last["items"]] == ["Home/50 percent.jpg"]
    assert last["next_cursor"] is None  # nothing from the Kyoto or 旅行 folders leaks in


def test_search_stays_inside_an_album(tmp_path: Path) -> None:
    client = _library(tmp_path)
    album = client.post("/api/v1/albums", json={"name": "Mixed"}).json()
    items = {r["relative_path"]: r["id"] for r in client.get("/api/v1/assets").json()["items"]}
    added = client.post(
        f"/api/v1/albums/{album['id']}/assets",
        json={"asset_ids": [items["Home/birthday cake.JPG"], items["Trip to Kyoto/IMG_0001.jpg"]], "base_rev": album["rev"]},
    )
    assert added.status_code == 200, added.text
    assert _names(client, album_id=album["id"], q="img") == ["Trip to Kyoto/IMG_0001.jpg"]
