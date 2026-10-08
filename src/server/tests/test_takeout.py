from __future__ import annotations

import shutil
import threading
import time
from io import BytesIO
from pathlib import Path

from PIL import Image
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from mosaicwave.config import Settings
from mosaicwave.library.takeout import import_takeout
from mosaicwave.main import create_app
from mosaicwave.models import Album, AlbumAsset, Asset
from authutil import authed_client, library_dir, scan_library


def _jpeg_bytes(color: tuple[int, int, int] = (200, 40, 40)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def _write_takeout(root: Path, jpeg: bytes) -> None:
    date_dir = root / "Google Photos" / "Photos from 2024"
    album_dir = root / "Google Photos" / "Trip to Kyoto"
    date_dir.mkdir(parents=True)
    album_dir.mkdir(parents=True)
    sidecar = """{
  "title": "IMG_1234.jpg",
  "photoTakenTime": { "timestamp": "1700000000", "formatted": "2023-11-14" },
  "geoData": { "latitude": 35.0, "longitude": 135.0, "altitude": 0.0 },
  "description": "Kyoto"
}
"""
    (date_dir / "IMG_1234.jpg").write_bytes(jpeg)
    (date_dir / "IMG_1234.jpg.json").write_text(sidecar, encoding="utf-8")
    (album_dir / "IMG_1234.jpg").write_bytes(jpeg)
    (album_dir / "IMG_1234.jpg.json").write_text(sidecar, encoding="utf-8")


def _wait_import(client: TestClient, path: str, timeout: float = 15) -> dict:
    started = client.post("/api/v1/import/takeout", json={"path": path})
    assert started.status_code == 200, started.text
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get("/api/v1/import/takeout/status").json()
        if body["status"] == "done":
            return body
        if body["status"] == "error":
            raise AssertionError(body.get("error") or body)
        time.sleep(0.05)
    raise TimeoutError(client.get("/api/v1/import/takeout/status").json())


def test_takeout_dedup_and_reimport(tmp_path: Path) -> None:
    takeout = tmp_path / "Takeout"
    dest = tmp_path / "library"
    dest.mkdir()
    _write_takeout(takeout, _jpeg_bytes())
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=takeout, source_dir=dest)
    app = create_app(settings)
    factory: sessionmaker = app.state.session_factory

    session = factory()
    try:
        source = import_takeout(session, takeout, dest_root=dest)
        session.commit()
        source_id = source.id
        assets = list(session.scalars(select(Asset).where(Asset.deleted_at.is_(None))))
        albums = list(session.scalars(select(Album).where(Album.deleted_at.is_(None))))
        links = list(session.scalars(select(AlbumAsset).where(AlbumAsset.deleted_at.is_(None))))
        assert len(assets) == 1
        assert len(albums) == 1
        assert albums[0].name == "Trip to Kyoto"
        assert len(links) == 1
        assert assets[0].relative_path == "IMG_1234.jpg"
        assert assets[0].content_hash
        assert assets[0].taken_at is not None
        assert assets[0].extra_json and "Kyoto" in assets[0].extra_json
        assert source.kind == "folder"
        assert Path(source.root_uri) == dest.resolve()
        first_id = assets[0].id
    finally:
        session.close()

    session = factory()
    try:
        import_takeout(session, takeout, dest_root=dest)
        session.commit()
        assets = list(session.scalars(select(Asset).where(Asset.deleted_at.is_(None))))
        albums = list(session.scalars(select(Album).where(Album.deleted_at.is_(None))))
        links = list(session.scalars(select(AlbumAsset).where(AlbumAsset.deleted_at.is_(None))))
        assert len(assets) == 1
        assert assets[0].id == first_id
        assert len(albums) == 1
        assert len(links) == 1
        assert source_id == assets[0].source_id
    finally:
        session.close()


def test_import_and_list_api(tmp_path: Path) -> None:
    takeout = tmp_path / "Takeout"
    _write_takeout(takeout, _jpeg_bytes())
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=takeout, source_dir=None)
    client = authed_client(settings)

    empty = client.get("/api/v1/assets")
    assert empty.status_code == 200
    assert empty.json() == {"items": [], "next_cursor": None}

    first = _wait_import(client, str(takeout))
    assert first["assets"] == 1
    assert first["albums"] == 1
    assert first["memberships"] == 1
    assert first["id"]
    jobs = client.get("/api/v1/jobs").json()
    assert jobs["items"][0]["id"] == first["id"]
    assert jobs["items"][0]["kind"] == "takeout_import"
    assert jobs["items"][0]["status"] == "done"
    events = client.get(f"/api/v1/jobs/{first['id']}/events").json()
    assert len(events) >= 2

    second = _wait_import(client, str(takeout))
    assert second["assets"] == 1
    assert second["source_id"] == first["source_id"]

    listed = client.get("/api/v1/assets")
    assert listed.status_code == 200
    body = listed.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["filename"] == "IMG_1234.jpg"
    assert body["items"][0]["taken_at"].endswith("Z")
    assert body["next_cursor"] is None
    asset_id = body["items"][0]["id"]
    shutil.rmtree(takeout)
    original = client.get(f"/api/v1/assets/{asset_id}/file")
    assert original.status_code == 200
    assert original.content == _jpeg_bytes()


def test_live_photo_video_uses_heic_sidecar(tmp_path: Path) -> None:
    takeout = tmp_path / "Takeout"
    photos = takeout / "Google Photos" / "Photos from 2021"
    photos.mkdir(parents=True)
    jpeg = _jpeg_bytes()
    (photos / "IMG_1.HEIC").write_bytes(jpeg)
    (photos / "IMG_1.MP4").write_bytes(b"not-a-real-video")
    sidecar = '{"photoTakenTime": {"timestamp": "1625116378"}}'
    (photos / "IMG_1.HEIC.supplemental-metadata.json").write_text(sidecar, encoding="utf-8")
    dest = tmp_path / "library"
    dest.mkdir()
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=dest)
    app = create_app(settings)
    session = app.state.session_factory()
    try:
        import_takeout(session, takeout, dest_root=dest)
        session.commit()
        rows = list(session.scalars(select(Asset).where(Asset.deleted_at.is_(None))))
    finally:
        session.close()
    by_name = {Path(row.relative_path).name: row for row in rows}
    assert "IMG_1.MP4" in by_name
    assert by_name["IMG_1.MP4"].taken_at is not None
    assert by_name["IMG_1.MP4"].taken_at.year == 2021
    assert by_name["IMG_1.HEIC"].taken_at is not None
    assert by_name["IMG_1.HEIC"].taken_at.year == 2021


def test_folder_scan_keeps_duplicate_paths(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    client = authed_client(settings)
    folder = library_dir(settings, client)
    (folder / "a").mkdir(parents=True)
    (folder / "b").mkdir()
    jpeg = _jpeg_bytes()
    (folder / "a" / "x.jpg").write_bytes(jpeg)
    (folder / "b" / "x.jpg").write_bytes(jpeg)
    res = scan_library(client)
    assert res["asset_count"] == 2
    listed = client.get("/api/v1/assets").json()
    assert len(listed["items"]) == 2


def test_import_from_zip_without_extract(tmp_path: Path) -> None:
    import zipfile

    dump = tmp_path / "download"
    dump.mkdir()
    jpeg = _jpeg_bytes()
    sidecar = """{
  "title": "IMG_1234.jpg",
  "photoTakenTime": { "timestamp": "1700000000", "formatted": "2023-11-14" }
}
"""
    with zipfile.ZipFile(dump / "takeout-20260101T000000Z-1-001.zip", "w") as zf:
        zf.writestr("Takeout/Google Photos/Photos from 2024/IMG_1234.jpg", jpeg)
        zf.writestr("Takeout/Google Photos/Photos from 2024/IMG_1234.jpg.json", sidecar)
        zf.writestr("Takeout/Google Photos/Trip to Kyoto/IMG_1234.jpg", jpeg)
        zf.writestr("Takeout/Google Photos/Trip to Kyoto/IMG_1234.jpg.json", sidecar)
    (dump / "clip-002.mov").write_bytes(b"video-bytes")
    with zipfile.ZipFile(dump / "takeout-20260101T000000Z-1-003.zip", "w") as zf:
        zf.writestr(
            "Takeout/Google Photos/Photos from 2024/clip.mov.supplemental-metadata.json",
            "{}",
        )

    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    client = authed_client(settings)
    body = _wait_import(client, str(dump))
    assert body["assets"] == 2
    assert body["albums"] == 1
    inspect = client.post("/api/v1/import/takeout/inspect", json={"path": str(dump)})
    assert inspect.json()["ready"] is True
    assert inspect.json()["sidecar_without_media_count"] == 0


def test_import_post_returns_before_hash_finishes(tmp_path: Path, monkeypatch) -> None:
    takeout = tmp_path / "Takeout"
    _write_takeout(takeout, _jpeg_bytes())
    released = threading.Event()

    def slow_hash(_store, _rel) -> str:
        released.wait(timeout=5)
        return "ab" * 32

    monkeypatch.setattr("mosaicwave.library.takeout.sha256_file", slow_hash)
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    client = authed_client(settings)
    t0 = time.monotonic()
    res = client.post("/api/v1/import/takeout", json={"path": str(takeout)})
    assert time.monotonic() - t0 < 1.0
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "running"
    released.set()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        body = client.get("/api/v1/import/takeout/status").json()
        if body["status"] == "done":
            assert body["assets"] == 1
            return
        if body["status"] == "error":
            raise AssertionError(body.get("error") or body)
        time.sleep(0.05)
    raise TimeoutError(client.get("/api/v1/import/takeout/status").json())


def _push_file(client, job_id: str, rel: str, data: bytes):
    return client.post(
        f"/api/v1/import/takeout/push/{job_id}/file",
        params={"relative_path": rel},
        content=data,
        headers={"Content-Type": "application/octet-stream"},
    )


def test_takeout_push_client_upload(tmp_path: Path) -> None:
    takeout = tmp_path / "Takeout"
    _write_takeout(takeout, _jpeg_bytes())
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    client = authed_client(settings)
    files = [p for p in takeout.rglob("*") if p.is_file()]
    start = client.post("/api/v1/import/takeout/push", json={"file_count": len(files)})
    assert start.status_code == 200, start.text
    body = start.json()
    assert body["status"] == "running"
    assert body["mode"] == "push"
    assert body["phase"] == "receiving"
    job_id = body["id"]
    for path in files:
        rel = path.relative_to(takeout).as_posix()
        res = _push_file(client, job_id, rel, path.read_bytes())
        assert res.status_code == 200, res.text
    bad = _push_file(client, job_id, "../escape.jpg", b"x")
    assert bad.status_code == 400
    fin = client.post(f"/api/v1/import/takeout/push/{job_id}/finish")
    assert fin.status_code == 200, fin.text
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        status = client.get("/api/v1/import/takeout/status").json()
        if status["status"] == "done":
            assert status["assets"] == 1
            assert status["albums"] == 1
            return
        if status["status"] == "error":
            raise AssertionError(status.get("error") or status)
        time.sleep(0.05)
    raise TimeoutError(client.get("/api/v1/import/takeout/status").json())


def test_takeout_push_cancel(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    client = authed_client(settings)
    start = client.post("/api/v1/import/takeout/push", json={"file_count": 1})
    assert start.status_code == 200, start.text
    job_id = start.json()["id"]
    cancel = client.post(f"/api/v1/import/takeout/push/{job_id}/cancel")
    assert cancel.status_code == 200, cancel.text
    assert cancel.json()["status"] == "error"
    again = client.post("/api/v1/import/takeout/push", json={"file_count": 1})
    assert again.status_code == 200, again.text


def test_takeout_push_busy_tells_cancel(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    client = authed_client(settings)
    start = client.post("/api/v1/import/takeout/push", json={"file_count": 1})
    assert start.status_code == 200, start.text
    clash = client.post("/api/v1/import/takeout/push", json={"file_count": 1})
    assert clash.status_code == 409, clash.text
    detail = clash.json()["detail"]
    assert "Cancel upload" in detail
    client.post(f"/api/v1/import/takeout/push/{start.json()['id']}/cancel")


def test_takeout_push_saves_windows_colon_path(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    client = authed_client(settings)
    start = client.post("/api/v1/import/takeout/push", json={"file_count": 1})
    assert start.status_code == 200, start.text
    job_id = start.json()["id"]
    res = _push_file(client, job_id, "Album: Morning/note.json", b"{}")
    assert res.status_code == 200, res.text
    client.post(f"/api/v1/import/takeout/push/{job_id}/cancel")


def test_takeout_push_plus_filename(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    client = authed_client(settings)
    start = client.post("/api/v1/import/takeout/push", json={"file_count": 1})
    assert start.status_code == 200, start.text
    job_id = start.json()["id"]
    rel = "2021-07-14+14-56-10+MJG0+3840x2160+30fps-006.mov"
    res = _push_file(client, job_id, rel, b"mov")
    assert res.status_code == 200, res.text
    client.post(f"/api/v1/import/takeout/push/{job_id}/cancel")


def test_takeout_push_size_cap_above_4gb_zip_split() -> None:
    from mosaicwave.library.takeoutpush import MAX_PUSH_BYTES

    assert MAX_PUSH_BYTES > 4 * 1024 * 1024 * 1024


def test_takeout_push_file_larger_than_multipart_cap(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    client = authed_client(settings)
    start = client.post("/api/v1/import/takeout/push", json={"file_count": 1})
    assert start.status_code == 200, start.text
    job_id = start.json()["id"]
    payload = b"a" * (1024 * 1024 + 64)
    res = _push_file(client, job_id, "takeout-big.zip", payload)
    assert res.status_code == 200, res.text
    client.post(f"/api/v1/import/takeout/push/{job_id}/cancel")


def test_takeout_sanitizes_colon_filename(tmp_path: Path) -> None:
    takeout = tmp_path / "Takeout"
    dest = tmp_path / "library"
    dest.mkdir()
    photos = takeout / "Google Photos" / "Photos from 2024"
    photos.mkdir(parents=True)
    (photos / "Photo from 2:15 PM.jpg").write_bytes(_jpeg_bytes(color=(10, 80, 200)))
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=takeout, source_dir=dest)
    app = create_app(settings)
    session = app.state.session_factory()
    try:
        import_takeout(session, takeout, dest_root=dest)
        session.commit()
        assets = list(session.scalars(select(Asset).where(Asset.deleted_at.is_(None))))
        assert len(assets) == 1
        assert assets[0].relative_path == "Photo from 2_15 PM.jpg"
        assert (dest / "Photo from 2_15 PM.jpg").is_file()
        assert not (dest / "Photo from 2:15 PM.jpg").exists()
    finally:
        session.close()


def test_job_failure_includes_current_path() -> None:
    from mosaicwave.library.importjob import _job_failure_message

    assert (
        _job_failure_message("Album/2:15 PM.jpg", OSError(22, "Invalid argument"))
        == "Album/2:15 PM.jpg: [Errno 22] Invalid argument"
    )
    assert _job_failure_message(None, OSError(22, "Invalid argument")) == "[Errno 22] Invalid argument"

