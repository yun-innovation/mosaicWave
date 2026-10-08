from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image
from fastapi.testclient import TestClient

from mosaicwave.config import Settings
from authutil import ADMIN_PASS, authed_client, library_dir, library_id, scan_library


def _jpeg_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (8, 8), (40, 120, 200)).save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def _member(admin: TestClient) -> tuple[TestClient, str]:
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
    return member, created.json()["id"]


def test_owner_can_share_read_and_write(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    admin = authed_client(settings)
    lib = library_dir(settings, admin)
    (lib / "kept.jpg").write_bytes(_jpeg_bytes())
    scan_library(admin)
    source_id = library_id(admin)
    asset_id = admin.get("/api/v1/assets").json()["items"][0]["id"]
    album = admin.post("/api/v1/albums", json={"name": "SharedTrip"})
    assert album.status_code == 200, album.text
    album_id = album.json()["id"]
    add = admin.post(f"/api/v1/albums/{album_id}/assets", json={"asset_ids": [asset_id]})
    assert add.status_code == 200, add.text

    member, member_id = _member(admin)
    listed = member.get("/api/v1/users")
    assert listed.status_code == 200
    assert {row["username"] for row in listed.json()} >= {"admin", "pat"}

    assert member.get("/api/v1/assets").json()["items"] == []
    assert member.get(f"/api/v1/assets/{asset_id}").status_code == 404
    assert member.get("/api/v1/albums").json() == []

    grants = admin.put(
        f"/api/v1/sources/{source_id}/grants",
        json={"grants": [{"user_id": member_id, "perm": "read"}]},
    )
    assert grants.status_code == 200, grants.text
    assert grants.json()[0]["user_id"] == member_id
    assert grants.json()[0]["perm"] == "read"
    got = admin.get(f"/api/v1/sources/{source_id}/grants")
    assert got.status_code == 200
    assert len(got.json()) == 1

    stranger = member.put(
        f"/api/v1/sources/{source_id}/grants",
        json={"grants": [{"user_id": member_id, "perm": "write"}]},
    )
    assert stranger.status_code == 404

    names = {item["filename"] for item in member.get("/api/v1/assets").json()["items"]}
    assert names == {"kept.jpg"}
    assert member.get(f"/api/v1/assets/{asset_id}").status_code == 200
    assert member.get(f"/api/v1/assets/{asset_id}/thumb").status_code == 200
    albums = member.get("/api/v1/albums").json()
    assert {row["name"] for row in albums} == {"SharedTrip"}
    assert member.get(f"/api/v1/albums/{album_id}").status_code == 200
    sources = member.get("/api/v1/sources").json()
    assert len(sources) == 2
    shared = next(row for row in sources if not row["owned"])
    assert shared["id"] == source_id
    assert shared["perm"] == "read"
    own = next(row for row in sources if row["owned"])
    assert own["id"] == library_id(member)

    blocked_upload = member.post(
        "/api/v1/assets",
        data={"source_id": source_id},
        files={"file": ("nope.jpg", _jpeg_bytes(), "image/jpeg")},
    )
    assert blocked_upload.status_code == 404
    blocked_patch = member.patch(f"/api/v1/albums/{album_id}", json={"name": "Nope"})
    assert blocked_patch.status_code == 404

    pulled = member.get("/api/v1/sync/changes", params={"since_rev": 0})
    assert pulled.status_code == 200
    sync_names = {Path(a["relative_path"]).name for a in pulled.json()["assets"]}
    assert "kept.jpg" in sync_names
    assert any(row["id"] == album_id for row in pulled.json()["albums"])

    write = admin.put(
        f"/api/v1/sources/{source_id}/grants",
        json={"grants": [{"user_id": member_id, "perm": "write"}]},
    )
    assert write.status_code == 200
    assert write.json()[0]["perm"] == "write"
    upload = member.post(
        "/api/v1/assets",
        data={"source_id": source_id},
        files={"file": ("from-pat.jpg", _jpeg_bytes(), "image/jpeg")},
    )
    assert upload.status_code == 200, upload.text
    renamed = member.patch(f"/api/v1/albums/{album_id}", json={"name": "PatEdit"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "PatEdit"
    admin_names = {item["filename"] for item in admin.get("/api/v1/assets").json()["items"]}
    assert admin_names == {"kept.jpg", "from-pat.jpg"}

    revoked = admin.put(f"/api/v1/sources/{source_id}/grants", json={"grants": []})
    assert revoked.status_code == 200
    assert revoked.json() == []
    assert member.get("/api/v1/assets").json()["items"] == []
    assert member.get(f"/api/v1/assets/{asset_id}").status_code == 404
    assert member.get("/api/v1/albums").json() == []

    again = admin.put(
        f"/api/v1/sources/{source_id}/grants",
        json={"grants": [{"user_id": member_id, "perm": "read"}]},
    )
    assert again.status_code == 200
    assert member.get(f"/api/v1/assets/{asset_id}").status_code == 200

    bad_perm = admin.put(
        f"/api/v1/sources/{source_id}/grants",
        json={"grants": [{"user_id": member_id, "perm": "admin"}]},
    )
    assert bad_perm.status_code == 400
    self_grant = admin.put(
        f"/api/v1/sources/{source_id}/grants",
        json={"grants": [{"user_id": admin.get("/api/v1/me").json()["id"], "perm": "read"}]},
    )
    assert self_grant.status_code == 400
