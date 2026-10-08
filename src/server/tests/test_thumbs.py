from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from mosaicwave.config import Settings
from authutil import authed_client, library_dir, scan_library


def _jpeg_bytes(*, size: tuple[int, int] = (32, 24), color=(200, 40, 40), exif: Image.Exif | None = None) -> bytes:
    buf = BytesIO()
    img = Image.new("RGB", size, color)
    kwargs: dict = {"format": "JPEG", "quality": 80}
    if exif is not None:
        kwargs["exif"] = exif
    img.save(buf, **kwargs)
    return buf.getvalue()


def _wait_job(client: TestClient, job_id: str, timeout: float = 15) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/v1/jobs/{job_id}").json()
        if body["status"] == "done":
            return body
        if body["status"] == "error":
            raise AssertionError(body.get("error") or body)
        time.sleep(0.05)
    raise TimeoutError(client.get(f"/api/v1/jobs/{job_id}").json())


def _client(tmp_path: Path) -> tuple[Settings, TestClient]:
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    return settings, authed_client(settings)


def test_thumb_file_detail_and_range(tmp_path: Path) -> None:
    settings, client = _client(tmp_path)
    folder = library_dir(settings, client)
    jpeg = _jpeg_bytes(size=(80, 60))
    (folder / "shot.jpg").write_bytes(jpeg)
    scan_library(client)
    listed = client.get("/api/v1/assets").json()["items"]
    assert len(listed) == 1
    asset_id = listed[0]["id"]

    meta = client.get(f"/api/v1/assets/{asset_id}")
    assert meta.status_code == 200
    body = meta.json()
    assert body["filename"] == "shot.jpg"
    assert body["size"] == len(jpeg)

    thumb = client.get(f"/api/v1/assets/{asset_id}/thumb")
    assert thumb.status_code == 200
    assert thumb.headers["content-type"].startswith("image/jpeg")
    image = Image.open(BytesIO(thumb.content))
    assert image.format == "JPEG"
    assert max(image.size) <= 400

    again = client.get(f"/api/v1/assets/{asset_id}")
    assert again.json()["thumb_rev"] == 1

    original = client.get(f"/api/v1/assets/{asset_id}/file")
    assert original.status_code == 200
    assert original.content == jpeg

    partial = client.get(
        f"/api/v1/assets/{asset_id}/file",
        headers={"Range": "bytes=0-9"},
    )
    assert partial.status_code == 206
    assert partial.content == jpeg[:10]
    assert partial.headers["content-range"].startswith("bytes 0-9/")

    mid = client.get(
        f"/api/v1/assets/{asset_id}/file",
        headers={"Range": "bytes=10-19"},
    )
    assert mid.status_code == 206
    assert mid.content == jpeg[10:20]

    download = client.get(f"/api/v1/assets/{asset_id}/file", params={"download": "1"})
    assert download.status_code == 200
    assert "attachment" in download.headers.get("content-disposition", "")

    missing = client.get("/api/v1/assets/not-a-real-id")
    assert missing.status_code == 404


def test_video_thumb_is_placeholder(tmp_path: Path) -> None:
    settings, client = _client(tmp_path)
    folder = library_dir(settings, client)
    (folder / "clip.mp4").write_bytes(b"not-a-real-video")
    scan_library(client)
    asset_id = client.get("/api/v1/assets").json()["items"][0]["id"]
    thumb = client.get(f"/api/v1/assets/{asset_id}/thumb")
    assert thumb.status_code == 200
    image = Image.open(BytesIO(thumb.content))
    assert image.size == (400, 300)
    from mosaicwave.library.thumbs import is_placeholder_jpeg

    assert is_placeholder_jpeg(thumb.content)


def test_heic_thumb_is_photo(tmp_path: Path) -> None:
    pillow_heif = pytest.importorskip("pillow_heif")
    pillow_heif.register_heif_opener()
    settings, client = _client(tmp_path)
    folder = library_dir(settings, client)
    buf = BytesIO()
    Image.new("RGB", (48, 32), (20, 180, 80)).save(buf, format="HEIF")
    (folder / "shot.heic").write_bytes(buf.getvalue())
    scan_library(client)
    asset_id = client.get("/api/v1/assets").json()["items"][0]["id"]
    thumb = client.get(f"/api/v1/assets/{asset_id}/thumb")
    assert thumb.status_code == 200
    image = Image.open(BytesIO(thumb.content))
    assert image.format == "JPEG"
    assert max(image.size) <= 400
    from mosaicwave.library.thumbs import is_placeholder_jpeg

    assert not is_placeholder_jpeg(thumb.content)
    preview = client.get(f"/api/v1/assets/{asset_id}/preview")
    assert preview.status_code == 200
    preview_image = Image.open(BytesIO(preview.content))
    assert preview_image.format == "JPEG"
    assert preview.headers["content-type"].startswith("image/jpeg")
    listed = client.get("/api/v1/assets").json()["items"][0]
    assert listed["mime"] == "image/heic"


def test_video_thumb_uses_ffmpeg_frame(tmp_path: Path) -> None:
    import shutil
    import subprocess

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg not on PATH")
    settings, client = _client(tmp_path)
    folder = library_dir(settings, client)
    clip = folder / "clip.mp4"
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=red:s=320x240:d=1",
        "-pix_fmt",
        "yuv420p",
        str(clip),
    ]
    kwargs: dict = {"check": True, "timeout": 20}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.run(cmd, **kwargs)
    scan_library(client)
    asset_id = client.get("/api/v1/assets").json()["items"][0]["id"]
    thumb = client.get(f"/api/v1/assets/{asset_id}/thumb")
    assert thumb.status_code == 200
    image = Image.open(BytesIO(thumb.content))
    assert image.format == "JPEG"
    from mosaicwave.library.thumbs import is_placeholder_jpeg

    assert not is_placeholder_jpeg(thumb.content)
    r, g, b = image.resize((1, 1)).getpixel((0, 0))
    assert r > 180 and g < 80 and b < 80


def test_run_ffmpeg_uses_update_flag(monkeypatch, tmp_path: Path) -> None:
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"not-a-video")
    dest = tmp_path / "frame.jpg"
    seen: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        seen.append(list(cmd))
        Path(cmd[-1]).write_bytes(_jpeg_bytes())

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr("mosaicwave.library.thumbs.ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr("mosaicwave.library.thumbs.subprocess.run", fake_run)
    from mosaicwave.library.thumbs import _run_ffmpeg

    assert _run_ffmpeg(str(src), str(dest))
    assert seen
    assert "-update" in seen[0]
    assert "-nostdin" in seen[0]
    assert "-threads" in seen[0]


def test_placeholder_thumb_not_rerendered(tmp_path: Path, monkeypatch) -> None:
    import mosaicwave.library.thumbs as thumbs

    thumbs.clear_placeholder_skips()
    calls = {"n": 0}
    original = thumbs.render_thumb_jpeg

    def counted(store, rel):
        calls["n"] += 1
        return original(store, rel)

    monkeypatch.setattr(thumbs, "render_thumb_jpeg", counted)
    settings, client = _client(tmp_path)
    folder = library_dir(settings, client)
    (folder / "clip.mp4").write_bytes(b"not-a-real-video")
    scan_library(client)
    asset_id = client.get("/api/v1/assets").json()["items"][0]["id"]
    first = client.get(f"/api/v1/assets/{asset_id}/thumb")
    second = client.get(f"/api/v1/assets/{asset_id}/thumb")
    assert first.status_code == 200
    assert second.status_code == 200
    assert calls["n"] == 1
    assert thumbs.is_placeholder_jpeg(first.content)


def test_thumb_batch_job(tmp_path: Path) -> None:
    settings, client = _client(tmp_path)
    folder = library_dir(settings, client)
    (folder / "shot.jpg").write_bytes(_jpeg_bytes())
    scan_library(client)
    started = client.post("/api/v1/thumbs/generate")
    assert started.status_code == 200, started.text
    assert started.json()["kind"] == "thumb_batch"
    assert started.json()["status"] == "running"
    done = _wait_job(client, started.json()["id"])
    assert done["processed"] == 1
    asset_id = client.get("/api/v1/assets").json()["items"][0]["id"]
    assert client.get(f"/api/v1/assets/{asset_id}").json()["thumb_rev"] >= 1


def test_exif_taken_at_and_mtime_fallback(tmp_path: Path) -> None:
    settings, client = _client(tmp_path)
    folder = library_dir(settings, client)
    exif = Image.Exif()
    exif[36867] = "2022:06:15 12:30:00"
    (folder / "dated.jpg").write_bytes(_jpeg_bytes(exif=exif))
    plain = folder / "plain.jpg"
    plain.write_bytes(_jpeg_bytes(color=(10, 20, 30)))
    stamp = datetime(2021, 3, 4, 5, 6, 7, tzinfo=timezone.utc).timestamp()
    import os

    os.utime(plain, (stamp, stamp))

    scan_library(client)
    items = {row["filename"]: row for row in client.get("/api/v1/assets").json()["items"]}
    dated = items["dated.jpg"]["taken_at"]
    assert dated is not None
    assert dated.startswith("2022-06-15T12:30:00")
    plain_taken = items["plain.jpg"]["taken_at"]
    assert plain_taken is not None
    assert plain_taken.startswith("2021-03-04T05:06:07")


def test_thumb_sqlite_locked_returns_jpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3

    from sqlalchemy.exc import OperationalError

    settings, client = _client(tmp_path)
    folder = library_dir(settings, client)
    (folder / "shot.jpg").write_bytes(_jpeg_bytes())
    scan_library(client)
    asset_id = client.get("/api/v1/assets").json()["items"][0]["id"]

    def boom(*_a: object, **_k: object) -> str:
        raise OperationalError("UPDATE asset", {}, sqlite3.OperationalError("database is locked"))

    monkeypatch.setattr("mosaicwave.api.v1.library.ensure_thumb", boom)
    thumb = client.get(f"/api/v1/assets/{asset_id}/thumb")
    assert thumb.status_code == 200, thumb.text
    assert thumb.headers["content-type"].startswith("image/jpeg")
    assert "max-age=0" in thumb.headers.get("cache-control", "")
    Image.open(BytesIO(thumb.content))
