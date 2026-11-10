"""DELETE /assets/{id}: the file and thumbnails go, albums lose it, the row stays as a tombstone with its hash."""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select, update

from authutil import authed_client, library_dir, library_session, scan_library
from mosaicwave.config import Settings
from mosaicwave.models import Album, AlbumAsset, Asset

NAMES = ["a/one.jpg", "a/two.jpg", "three.jpg"]


def _library(tmp_path: Path) -> tuple[TestClient, Path, dict[str, bytes]]:
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    client = authed_client(settings)
    folder = library_dir(settings, client)
    sent: dict[str, bytes] = {}
    for index, rel in enumerate(NAMES):
        buf = BytesIO()
        Image.new("RGB", (16, 12), (index * 70, 90, 160)).save(buf, format="JPEG")
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(buf.getvalue())
        sent[rel] = buf.getvalue()
    scan_library(client)
    return client, folder, sent


def _ids(client: TestClient) -> dict[str, str]:
    items = client.get("/api/v1/assets", params={"limit": 50}).json()["items"]
    return {row["relative_path"]: row["id"] for row in items}


def test_delete_removes_file_and_hides_the_photo(tmp_path: Path) -> None:
    client, folder, _ = _library(tmp_path)
    ids = _ids(client)
    victim = ids["a/one.jpg"]
    assert client.get(f"/api/v1/assets/{victim}/thumb").status_code == 200  # makes a thumbnail to remove

    res = client.delete(f"/api/v1/assets/{victim}")
    assert res.status_code == 200, res.text
    assert not (folder / "a" / "one.jpg").exists()
    assert (folder / "a" / "two.jpg").exists() and (folder / "three.jpg").exists()
    assert client.get(f"/api/v1/assets/{victim}").status_code == 404
    assert sorted(_ids(client)) == ["a/two.jpg", "three.jpg"]
    assert client.delete(f"/api/v1/assets/{victim}").status_code == 404  # again: gone


def test_tombstone_keeps_the_hash_even_when_the_scan_never_stored_one(tmp_path: Path) -> None:
    client, _folder, sent = _library(tmp_path)
    victim = _ids(client)["three.jpg"]
    with library_session(client) as session:
        session.execute(update(Asset).where(Asset.id == victim).values(content_hash=None))
        session.commit()
    assert client.delete(f"/api/v1/assets/{victim}").status_code == 200
    with library_session(client) as session:
        row = session.get(Asset, victim)
        assert row is not None and row.deleted_at is not None
        assert row.content_hash == hashlib.sha256(sent["three.jpg"]).hexdigest()
        assert row.rev > 0


def test_a_rescan_does_not_bring_it_back(tmp_path: Path) -> None:
    client, _folder, _ = _library(tmp_path)
    victim = _ids(client)["a/two.jpg"]
    client.delete(f"/api/v1/assets/{victim}")
    scan_library(client)
    assert "a/two.jpg" not in _ids(client)


def test_albums_lose_the_photo_and_the_count_and_cover_follow(tmp_path: Path) -> None:
    client, _folder, _ = _library(tmp_path)
    ids = _ids(client)
    album = client.post("/api/v1/albums", json={"name": "Trip"}).json()
    added = client.post(
        f"/api/v1/albums/{album['id']}/assets", json={"asset_ids": [ids["a/one.jpg"], ids["a/two.jpg"]]}
    )
    assert added.status_code == 200 and added.json()["asset_count"] == 2
    cover = added.json()["cover_asset_id"]
    client.delete(f"/api/v1/assets/{cover}")
    after = client.get(f"/api/v1/albums/{album['id']}").json()
    assert after["asset_count"] == 1
    assert after["cover_asset_id"] and after["cover_asset_id"] != cover
    with library_session(client) as session:
        links = session.scalars(select(AlbumAsset).where(AlbumAsset.asset_id == cover)).all()
        assert links and all(link.deleted_at is not None for link in links)
        assert session.scalar(select(Album).where(Album.id == album["id"])) is not None


def test_a_file_that_cannot_be_deleted_changes_nothing(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client, folder, _ = _library(tmp_path)
    victim = _ids(client)["three.jpg"]

    def refuse(self, rel):  # type: ignore[no-untyped-def]
        raise PermissionError("read-only")

    monkeypatch.setattr("mosaicwave.storage.filestore.LocalFileStore.delete", refuse)
    res = client.delete(f"/api/v1/assets/{victim}")
    assert res.status_code == 409 and res.json()["code"] == "asset_delete_failed"
    assert (folder / "three.jpg").exists()
    assert client.get(f"/api/v1/assets/{victim}").status_code == 200


def test_needs_a_session_and_other_people_get_404(tmp_path: Path) -> None:
    client, _folder, _ = _library(tmp_path)
    victim = _ids(client)["three.jpg"]
    assert TestClient(client.app).delete(f"/api/v1/assets/{victim}").status_code == 401
    client.put("/api/v1/users", json={"username": "pat", "password": "password1", "role": "member"})
    pat = TestClient(client.app)
    assert pat.post("/api/v1/auth/login", json={"username": "pat", "password": "password1"}).status_code == 200
    assert pat.delete(f"/api/v1/assets/{victim}").status_code == 404
    assert client.get(f"/api/v1/assets/{victim}").status_code == 200
