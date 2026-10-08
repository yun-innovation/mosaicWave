from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from mosaicwave.config import Settings
from mosaicwave.main import create_app

ADMIN_USER = "admin"
ADMIN_PASS = "secret12"


def setup_admin(client: TestClient) -> TestClient:
    status = client.get("/api/v1/auth/status")
    assert status.status_code == 200, status.text
    if status.json()["needs_setup"]:
        res = client.post(
            "/api/v1/auth/setup",
            json={
                "username": ADMIN_USER,
                "password": ADMIN_PASS,
                "display_name": "Admin",
            },
        )
        assert res.status_code == 200, res.text
        return client
    res = client.post(
        "/api/v1/auth/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASS},
    )
    assert res.status_code == 200, res.text
    return client


def authed_client(settings: Settings) -> TestClient:
    return setup_admin(TestClient(create_app(settings)))


def me_id(client: TestClient) -> str:
    return client.get("/api/v1/me").json()["id"]


def library_dir(settings: Settings, client: TestClient) -> Path:
    path = (settings.data_dir / "libraries" / me_id(client)).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def library_id(client: TestClient) -> str:
    rows = client.get("/api/v1/sources").json()
    owned = [row for row in rows if row.get("owned")]
    assert len(owned) == 1, rows
    return owned[0]["id"]


def scan_library(client: TestClient) -> dict:
    res = client.post(f"/api/v1/sources/{library_id(client)}/scan")
    assert res.status_code == 200, res.text
    return res.json()
