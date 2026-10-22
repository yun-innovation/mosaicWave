from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image
from fastapi.testclient import TestClient

from mosaicwave.config import Settings
from authutil import authed_client, library_dir, library_id, scan_library


def _jpeg_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (8, 8), (40, 120, 200)).save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def _member(admin: TestClient) -> TestClient:
    created = admin.put(
        "/api/v1/users",
        json={
            "username": "pat",
            "password": "memberpass",
            "display_name": "Pat",
            "role": "member",
        },
    )
    assert created.status_code == 200, created.text
    member = TestClient(admin.app)
    login = member.post("/api/v1/auth/login", json={"username": "pat", "password": "memberpass"})
    assert login.status_code == 200
    return member


def test_libraries_are_isolated_per_user(tmp_path: Path) -> None:
    """Two users never see, edit or upload into each other's library (no grants any more)."""
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    admin = authed_client(settings)
    lib = library_dir(settings, admin)
    (lib / "kept.jpg").write_bytes(_jpeg_bytes())
    scan_library(admin)
    source_id = library_id(admin)
    asset_id = admin.get("/api/v1/assets").json()["items"][0]["id"]
    album_id = admin.post("/api/v1/albums", json={"name": "SharedTrip"}).json()["id"]
    assert (
        admin.post(f"/api/v1/albums/{album_id}/assets", json={"asset_ids": [asset_id]}).status_code
        == 200
    )

    member = _member(admin)
    listed = member.get("/api/v1/users")
    assert listed.status_code == 200
    assert {row["username"] for row in listed.json()} >= {"admin", "pat"}

    assert member.get("/api/v1/assets").json()["items"] == []
    assert member.get(f"/api/v1/assets/{asset_id}").status_code == 404
    assert member.get(f"/api/v1/assets/{asset_id}/thumb").status_code == 404
    assert member.get("/api/v1/albums").json() == []
    assert member.get(f"/api/v1/albums/{album_id}").status_code == 404
    assert member.patch(f"/api/v1/albums/{album_id}", json={"name": "Nope"}).status_code == 404

    sources = member.get("/api/v1/sources").json()
    assert len(sources) == 1 and sources[0]["owned"]
    assert sources[0]["id"] != source_id

    blocked = member.post(
        "/api/v1/assets",
        data={"source_id": source_id},
        files={"file": ("nope.jpg", _jpeg_bytes(), "image/jpeg")},
    )
    assert blocked.status_code == 404
    assert member.get("/api/v1/sync/changes", params={"since_rev": 0}).json()["assets"] == []

    # The grants API is gone.
    assert admin.get(f"/api/v1/sources/{source_id}/grants").status_code in (404, 405)
    assert (
        admin.put(f"/api/v1/sources/{source_id}/grants", json={"grants": []}).status_code
        in (404, 405)
    )

    # Each user has a separate database file with its own revision clock.
    db_root = settings.accounts_dir
    assert len(list(db_root.glob("*/library.db"))) == 2
    mine = admin.get("/api/v1/sync/changes", params={"since_rev": 0}).json()
    assert [a["id"] for a in mine["assets"]] == [asset_id]
    assert mine["server_rev"] > 0


def test_client_ahead_of_library_clock_pulls_everything(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    admin = authed_client(settings)
    (library_dir(settings, admin) / "kept.jpg").write_bytes(_jpeg_bytes())
    scan_library(admin)
    body = admin.get("/api/v1/sync/changes", params={"since_rev": 10_000}).json()
    assert len(body["assets"]) == 1
