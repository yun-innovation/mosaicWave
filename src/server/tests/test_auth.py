from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image
from fastapi.testclient import TestClient

from mosaicwave.config import Settings
from mosaicwave.main import create_app
from authutil import ADMIN_PASS, ADMIN_USER, authed_client, library_dir, library_id, scan_library


def _jpeg_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (8, 8), (40, 120, 200)).save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def test_health_open_assets_closed(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)))
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/assets").status_code == 401
    assert client.get("/api/v1/fs/list").status_code == 401
    assert (
        client.post(
            "/api/v1/import/takeout/push/x/file",
            params={"relative_path": "a.jpg"},
            content=b"x",
        ).status_code
        == 401
    )
    assert client.post("/api/v1/import/takeout/push/x/finish").status_code == 401


def test_setup_login_me_logout(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    client = TestClient(create_app(settings))
    status = client.get("/api/v1/auth/status").json()
    assert status["platform"] == "standalone"
    assert status["needs_setup"] is True
    assert status["me"] is None

    bad = client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": "short", "display_name": "A"},
    )
    assert bad.status_code == 400

    setup = client.post(
        "/api/v1/auth/setup",
        json={"username": ADMIN_USER, "password": ADMIN_PASS, "display_name": "Admin"},
    )
    assert setup.status_code == 200, setup.text
    assert setup.json()["role"] == "admin"
    assert client.get("/api/v1/me").json()["username"] == ADMIN_USER
    again = client.post(
        "/api/v1/auth/setup",
        json={"username": "other", "password": ADMIN_PASS, "display_name": "X"},
    )
    assert again.status_code == 409

    client.post("/api/v1/auth/logout")
    assert client.get("/api/v1/me").status_code == 401
    wrong = client.post("/api/v1/auth/login", json={"username": ADMIN_USER, "password": "wrongpass"})
    assert wrong.status_code == 401
    ok = client.post("/api/v1/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    assert ok.status_code == 200
    assert ok.json()["role"] == "admin"


def test_member_cannot_see_admin_library(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    admin = authed_client(settings)
    lib = library_dir(settings, admin)
    (lib / "kept.jpg").write_bytes(_jpeg_bytes())
    scan_library(admin)
    source_id = library_id(admin)
    hidden_asset_id = admin.get("/api/v1/assets").json()["items"][0]["id"]

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
    extra = admin.post("/api/v1/sources", json={"path": str(tmp_path / "other"), "kind": "folder"})
    assert extra.status_code == 409
    assert admin.delete(f"/api/v1/sources/{source_id}").status_code == 400

    member = TestClient(admin.app)
    login = member.post("/api/v1/auth/login", json={"username": "pat", "password": "memberpass"})
    assert login.status_code == 200
    listed = member.get("/api/v1/assets")
    assert listed.status_code == 200
    assert listed.json()["items"] == []
    assert member.get(f"/api/v1/assets/{hidden_asset_id}").status_code == 404
    assert member.get(f"/api/v1/assets/{hidden_asset_id}/thumb").status_code == 404
    assert member.get(f"/api/v1/assets/{hidden_asset_id}/preview").status_code == 404
    assert member.get("/api/v1/jobs").status_code == 200
    assert member.get("/api/v1/fs/list").status_code == 200

    member_lib = library_id(member)
    upload = member.post(
        "/api/v1/assets",
        data={"source_id": member_lib},
        files={"file": ("new.jpg", _jpeg_bytes(), "image/jpeg")},
    )
    assert upload.status_code == 200, upload.text
    assert upload.json()["filename"] == "new.jpg"
    after = {item["filename"] for item in member.get("/api/v1/assets").json()["items"]}
    assert after == {"new.jpg"}
    admin_names = {item["filename"] for item in admin.get("/api/v1/assets").json()["items"]}
    assert admin_names == {"kept.jpg"}

    blocked = member.post(
        "/api/v1/assets",
        data={"source_id": source_id},
        files={"file": ("nope.jpg", _jpeg_bytes(), "image/jpeg")},
    )
    assert blocked.status_code == 404



def test_qnap_cgi_http_then_direct(monkeypatch) -> None:
    xml = '<?xml version="1.0"?><QDocRoot><authPassed>1</authPassed><username>admin</username></QDocRoot>'
    monkeypatch.setattr("mosaicwave.auth.providers._qnap_cgi_http", lambda *_a, **_k: None)
    monkeypatch.setattr("mosaicwave.auth.providers._qnap_cgi_direct", lambda *_a, **_k: xml)
    from mosaicwave.auth.providers import _parse_qnap_auth, _qnap_cgi_get, _strip_cgi_body

    text = _qnap_cgi_get({"user": "admin", "pwd": "x"})
    assert _parse_qnap_auth(text or "") == "admin"
    wrapped = b"Content-Type: text/xml\n\n" + xml.encode()
    assert _parse_qnap_auth(_strip_cgi_body(wrapped)) == "admin"
    cdata = (
        '<?xml version="1.0"?><QDocRoot>'
        "<authPassed><![CDATA[1]]></authPassed>"
        "<username><![CDATA[admin]]></username></QDocRoot>"
    )
    assert _parse_qnap_auth(cdata) == "admin"


def test_qnap_setup_rejected(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        takeout_dir=None,
        source_dir=None,
        platform="qnap",
        session_secret="a" * 32,
    )
    client = TestClient(create_app(settings))
    setup = client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": ADMIN_PASS, "display_name": "A"},
    )
    assert setup.status_code == 400
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": ADMIN_PASS})
    assert login.status_code == 503
