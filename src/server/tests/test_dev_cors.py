"""Dev-only CORS: a Next dev page opened at a LAN address may call the API only when the debug scripts asked for it."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mosaicwave.config import Settings
from mosaicwave.main import create_app


def _preflight(tmp_path: Path, origin: str) -> object:
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    client = TestClient(create_app(settings))
    return client.options(
        "/api/v1/auth/status",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )


def test_lan_origin_is_refused_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MOSAICWAVE_DEV_HOST", raising=False)
    res = _preflight(tmp_path, "http://192.168.50.11:3000")
    assert res.status_code == 400  # type: ignore[attr-defined]


def test_lan_origin_is_allowed_when_the_dev_host_is_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOSAICWAVE_DEV_HOST", "0.0.0.0")
    res = _preflight(tmp_path, "http://192.168.50.11:3000")
    assert res.status_code == 200  # type: ignore[attr-defined]
    assert res.headers["access-control-allow-origin"] == "http://192.168.50.11:3000"  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "origin",
    ["http://example.com:3000", "http://192.168.50.11:8000", "https://192.168.50.11:3000", "http://8.8.8.8:3000"],
)
def test_other_origins_stay_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, origin: str) -> None:
    monkeypatch.setenv("MOSAICWAVE_DEV_HOST", "0.0.0.0")
    assert _preflight(tmp_path, origin).status_code == 400  # type: ignore[attr-defined]
