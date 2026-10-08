from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mosaicwave.config import Settings
from mosaicwave.main import create_app


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    return TestClient(create_app(settings))


def test_health_ok(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_docs(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200
    spec = client.get("/openapi.json")
    assert spec.status_code == 200
    paths = spec.json()["paths"]
    assert paths["/api/v1/health"]["get"]
    assert paths["/api/v1/assets"]["get"]
    assert paths["/api/v1/import/takeout"]["post"]
    assert paths["/api/v1/import/takeout/push"]["post"]
    assert paths["/api/v1/import/takeout/push/{job_id}/file"]["post"]
    assert paths["/api/v1/import/takeout/push/{job_id}/file"]["put"]
    assert paths["/api/v1/import/takeout/push/{job_id}/finish"]["post"]
    assert paths["/api/v1/import/takeout/inspect"]["post"]
    assert paths["/api/v1/import/takeout/status"]["get"]
    assert paths["/api/v1/jobs"]["get"]
    assert paths["/api/v1/fs/list"]["get"]
    assert paths["/api/v1/assets/{asset_id}"]["get"]
    assert paths["/api/v1/assets/{asset_id}/thumb"]["get"]
    assert paths["/api/v1/assets/{asset_id}/preview"]["get"]
    assert paths["/api/v1/assets/{asset_id}/file"]["get"]
    assert paths["/api/v1/thumbs/generate"]["post"]
    assert paths["/api/v1/auth/login"]["post"]
    assert paths["/api/v1/auth/setup"]["post"]
    assert paths["/api/v1/me"]["get"]
    assert paths["/api/v1/assets"]["post"]
    assert paths["/api/v1/sources/{source_id}/grants"]["put"]
    assert paths["/api/v1/sync/changes"]["get"]
    assert paths["/api/v1/settings"]["get"]
    assert paths["/api/v1/settings"]["put"]
    assert paths["/api/v1/albums"]["get"]
    assert paths["/api/v1/albums"]["post"]
    assert paths["/api/v1/albums/{album_id}"]["get"]
    assert paths["/api/v1/albums/{album_id}"]["patch"]
    assert paths["/api/v1/albums/{album_id}"]["delete"]
    assert paths["/api/v1/albums/{album_id}/assets"]["post"]
    assert paths["/api/v1/albums/{album_id}/assets/{asset_id}"]["delete"]


def test_web_root_serves_index_without_hiding_api(tmp_path: Path) -> None:
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html><title>mw</title>", encoding="utf-8")
    settings = Settings(
        data_dir=tmp_path / "data",
        takeout_dir=None,
        source_dir=None,
        web_root=web,
    )
    client = TestClient(create_app(settings))
    home = client.get("/")
    assert home.status_code == 200
    assert "mw" in home.text
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/docs").status_code == 200
