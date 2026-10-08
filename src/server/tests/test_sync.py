from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image
from fastapi.testclient import TestClient

from sqlalchemy import select

from mosaicwave.config import Settings
from mosaicwave.library.sync import assign_sync_rev
from mosaicwave.library.takeout import import_takeout
from mosaicwave.main import create_app
from mosaicwave.models import Asset, utcnow
from authutil import ADMIN_PASS, ADMIN_USER, authed_client, library_dir, library_id, scan_library, setup_admin


def _jpeg_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (8, 8), (200, 40, 40)).save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def test_sync_requires_auth(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)))
    assert client.get("/api/v1/sync/changes").status_code == 401


def test_sync_pull_assets_albums_and_watermark(tmp_path: Path) -> None:
    takeout = tmp_path / "Takeout"
    photos = takeout / "Google Photos" / "Photos from 2024"
    album = takeout / "Google Photos" / "Trip"
    photos.mkdir(parents=True)
    album.mkdir(parents=True)
    jpeg = _jpeg_bytes()
    (photos / "IMG_1.jpg").write_bytes(jpeg)
    (photos / "IMG_1.jpg.json").write_text(
        '{"title":"IMG_1.jpg","photoTakenTime":{"timestamp":"1700000000"}}',
        encoding="utf-8",
    )
    (album / "IMG_1.jpg").write_bytes(jpeg)
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=takeout, source_dir=None)
    client = authed_client(settings)
    dest = library_dir(settings, client)
    session = client.app.state.session_factory()
    try:
        import_takeout(session, takeout, dest_root=dest, owner_user_id=client.get("/api/v1/me").json()["id"])
        session.commit()
    finally:
        session.close()

    first = client.get("/api/v1/sync/changes", params={"since_rev": 0})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["assets"]
    assert body["albums"]
    assert body["album_assets"]
    assert body["next_cursor"] is None
    assert body["server_rev"] >= 1
    assert "root_uri" not in str(body)
    asset = body["assets"][0]
    assert asset["relative_path"].endswith("IMG_1.jpg")
    assert asset["rev"] >= 1
    assert "deleted_at" in asset

    again = client.get("/api/v1/sync/changes", params={"since_rev": body["server_rev"]})
    assert again.status_code == 200
    empty = again.json()
    assert empty["assets"] == []
    assert empty["albums"] == []
    assert empty["album_assets"] == []
    assert empty["server_rev"] == body["server_rev"]


def test_sync_pages_and_member_scope(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    admin = authed_client(settings)
    lib = library_dir(settings, admin)
    (lib / "a.jpg").write_bytes(_jpeg_bytes())
    (lib / "b.jpg").write_bytes(_jpeg_bytes())
    scan_library(admin)
    created = admin.put(
        "/api/v1/users",
        json={"username": "pat", "password": "memberpass", "display_name": "Pat", "role": "member"},
    )
    assert created.status_code == 200, created.text

    paged = admin.get("/api/v1/sync/changes", params={"since_rev": 0, "limit": 1})
    assert paged.status_code == 200
    page = paged.json()
    assert page["next_cursor"]
    total = len(page["assets"]) + len(page["albums"]) + len(page["album_assets"])
    assert total == 1
    rest = admin.get(
        "/api/v1/sync/changes",
        params={"since_rev": 0, "cursor": page["next_cursor"], "limit": 50},
    )
    assert rest.status_code == 200
    assert rest.json()["assets"] or rest.json()["albums"] or rest.json()["album_assets"]

    member = TestClient(admin.app)
    login = member.post("/api/v1/auth/login", json={"username": "pat", "password": "memberpass"})
    assert login.status_code == 200
    scoped = member.get("/api/v1/sync/changes", params={"since_rev": 0})
    assert scoped.status_code == 200
    names = {Path(a["relative_path"]).name for a in scoped.json()["assets"]}
    assert names == set()


def test_sync_includes_tombstones(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    client = authed_client(settings)
    folder = library_dir(settings, client)
    (folder / "gone.jpg").write_bytes(_jpeg_bytes())
    scan_library(client)
    session = client.app.state.session_factory()
    try:
        row = session.scalars(select(Asset)).one()
        row.deleted_at = utcnow()
        assign_sync_rev(session, row)
        session.commit()
        tomb_id = row.id
        tomb_rev = row.rev
    finally:
        session.close()
    body = client.get("/api/v1/sync/changes", params={"since_rev": 0}).json()
    found = next(a for a in body["assets"] if a["id"] == tomb_id)
    assert found["deleted_at"] is not None
    assert found["rev"] == tomb_rev


def _apply_replica(replica: dict[str, dict[str, dict]], body: dict) -> int:
    """Stand-in for a client SQLite/IndexedDB: upsert sync rows by id."""
    for kind in ("assets", "albums", "album_assets"):
        bucket = replica.setdefault(kind, {})
        for row in body[kind]:
            bucket[row["id"]] = row
    return int(body["server_rev"])


def test_second_client_list_thumb_and_sync(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    app = create_app(settings)
    first = setup_admin(TestClient(app))
    dest = library_dir(settings, first)
    (dest / "shot.jpg").write_bytes(_jpeg_bytes())
    scan_library(first)
    source_id = library_id(first)
    asset_id = first.get("/api/v1/assets").json()["items"][0]["id"]
    assert first.get(f"/api/v1/assets/{asset_id}/thumb").status_code == 200

    second = TestClient(app)
    login = second.post("/api/v1/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    assert login.status_code == 200
    listed = second.get("/api/v1/assets")
    assert listed.status_code == 200
    assert listed.json()["items"]
    assert second.get(f"/api/v1/assets/{asset_id}/thumb").status_code == 200

    replica: dict[str, dict[str, dict]] = {}
    pulled = second.get("/api/v1/sync/changes", params={"since_rev": 0})
    assert pulled.status_code == 200
    rev = _apply_replica(replica, pulled.json())
    assert replica["assets"][asset_id]["relative_path"].endswith("shot.jpg")
    assert replica["assets"][asset_id]["deleted_at"] is None

    uploaded = first.post(
        "/api/v1/assets",
        data={"source_id": source_id},
        files={"file": ("new.jpg", _jpeg_bytes(), "image/jpeg")},
    )
    assert uploaded.status_code == 200, uploaded.text
    new_id = uploaded.json()["id"]
    created = second.get("/api/v1/sync/changes", params={"since_rev": rev})
    assert created.status_code == 200
    rev = _apply_replica(replica, created.json())
    assert new_id in replica["assets"]
    assert replica["assets"][asset_id]["relative_path"].endswith("shot.jpg")

    session = app.state.session_factory()
    try:
        row = session.get(Asset, asset_id)
        assert row is not None
        row.deleted_at = utcnow()
        assign_sync_rev(session, row)
        session.commit()
    finally:
        session.close()
    updated = second.get("/api/v1/sync/changes", params={"since_rev": rev})
    assert updated.status_code == 200
    _apply_replica(replica, updated.json())
    assert replica["assets"][asset_id]["deleted_at"] is not None
    assert replica["assets"][new_id]["deleted_at"] is None
