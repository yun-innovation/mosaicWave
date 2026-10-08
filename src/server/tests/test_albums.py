from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from mosaicwave.config import Settings
from authutil import authed_client, library_dir, library_id, scan_library
from test_takeout import _jpeg_bytes, _wait_import, _write_takeout


def test_manual_album_crud_and_filter(tmp_path: Path) -> None:
    takeout = tmp_path / "Takeout"
    _write_takeout(takeout, _jpeg_bytes())
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    client = authed_client(settings)
    imported = _wait_import(client, str(takeout))
    assert imported["albums"] == 1
    asset_id = client.get("/api/v1/assets").json()["items"][0]["id"]

    empty_name = client.post("/api/v1/albums", json={"name": "  "})
    assert empty_name.status_code == 400

    created = client.post("/api/v1/albums", json={"name": " Weekend "})
    assert created.status_code == 200, created.text
    album = created.json()
    assert album["name"] == "Weekend"
    assert album["asset_count"] == 0
    album_id = album["id"]

    clash = client.post("/api/v1/albums", json={"name": "Weekend"})
    assert clash.status_code == 409

    listed = client.get("/api/v1/albums").json()
    names = {row["name"] for row in listed}
    assert "Weekend" in names
    assert "Trip to Kyoto" in names

    add = client.post(f"/api/v1/albums/{album_id}/assets", json={"asset_ids": [asset_id]})
    assert add.status_code == 200, add.text
    assert add.json()["asset_count"] == 1
    assert add.json()["cover_asset_id"] == asset_id

    filtered = client.get("/api/v1/assets", params={"album_id": album_id})
    assert filtered.status_code == 200
    assert [row["id"] for row in filtered.json()["items"]] == [asset_id]

    renamed = client.patch(f"/api/v1/albums/{album_id}", json={"name": "Holiday"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Holiday"

    gone = client.delete(f"/api/v1/albums/{album_id}/assets/{asset_id}")
    assert gone.status_code == 200
    assert client.get(f"/api/v1/albums/{album_id}").json()["asset_count"] == 0
    assert client.get("/api/v1/assets", params={"album_id": album_id}).json()["items"] == []

    deleted = client.delete(f"/api/v1/albums/{album_id}")
    assert deleted.status_code == 200
    assert client.get(f"/api/v1/albums/{album_id}").status_code == 404
    names_after = {row["name"] for row in client.get("/api/v1/albums").json()}
    assert "Holiday" not in names_after

    revived = client.post("/api/v1/albums", json={"name": "Holiday"})
    assert revived.status_code == 200
    assert revived.json()["id"] == album_id
    assert revived.json()["asset_count"] == 0


def test_album_hidden_from_other_user(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    admin = authed_client(settings)
    lib = library_dir(settings, admin)
    (lib / "kept.jpg").write_bytes(_jpeg_bytes())
    scan_library(admin)
    created = admin.post("/api/v1/albums", json={"name": "Secret"})
    assert created.status_code == 200, created.text
    album_id = created.json()["id"]
    admin.put(
        "/api/v1/users",
        json={"username": "pat", "password": "memberpass", "display_name": "Pat", "role": "member"},
    )
    member = TestClient(admin.app)
    login = member.post("/api/v1/auth/login", json={"username": "pat", "password": "memberpass"})
    assert login.status_code == 200
    assert member.get("/api/v1/albums").json() == []
    assert member.get(f"/api/v1/albums/{album_id}").status_code == 404
    assert member.get("/api/v1/assets", params={"album_id": album_id}).status_code == 404
    assert library_id(admin) != library_id(member)


def test_album_push_base_rev_and_client_id(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    client = authed_client(settings)
    created = client.post("/api/v1/albums", json={"name": "PushMe"})
    assert created.status_code == 200, created.text
    album = created.json()
    assert album["rev"] >= 1
    album_id = album["id"]
    rev = album["rev"]

    stale = client.patch(f"/api/v1/albums/{album_id}", json={"name": "Nope", "base_rev": 0})
    assert stale.status_code == 409
    assert "rev" in stale.json()["detail"]

    ok = client.patch(f"/api/v1/albums/{album_id}", json={"name": "Yes", "base_rev": rev})
    assert ok.status_code == 200, ok.text
    assert ok.json()["name"] == "Yes"
    assert ok.json()["rev"] > rev

    live = client.patch(f"/api/v1/albums/{album_id}", json={"name": "Live"})
    assert live.status_code == 200
    assert live.json()["name"] == "Live"
    current = live.json()["rev"]

    replica_id = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    pushed = client.post("/api/v1/albums", json={"id": replica_id, "name": "Offline"})
    assert pushed.status_code == 200, pushed.text
    assert pushed.json()["id"] == replica_id
    again = client.post("/api/v1/albums", json={"id": replica_id, "name": "Offline"})
    assert again.status_code == 200
    assert again.json()["id"] == replica_id
    assert again.json()["rev"] == pushed.json()["rev"]

    bad_id = client.post("/api/v1/albums", json={"id": "not-a-uuid", "name": "X"})
    assert bad_id.status_code == 400

    gone = client.delete(f"/api/v1/albums/{album_id}", params={"base_rev": rev})
    assert gone.status_code == 409
    assert client.delete(f"/api/v1/albums/{album_id}", params={"base_rev": current}).status_code == 200
