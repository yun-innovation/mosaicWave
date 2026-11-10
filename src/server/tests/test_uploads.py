"""/uploads: check, create, resume, finish into Uploads/{year}, removed hashes, limits."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from authutil import authed_client, library_dir
from mosaicwave.config import Settings
from mosaicwave.library import uploads

OCTETS = "application/offset+octet-stream"


def _jpeg(color: int, taken: str | None = None) -> bytes:
    buf = BytesIO()
    image = Image.new("RGB", (64, 48), (color, 80, 120))
    if taken:
        exif = Image.Exif()
        exif[0x8769] = {0x9003: taken}  # DateTimeOriginal
        image.save(buf, format="JPEG", exif=exif)
    else:
        image.save(buf, format="JPEG")
    return buf.getvalue()


def _png(color: int) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (8, 8), (color, 1, 2)).save(buf, format="PNG")
    return buf.getvalue()


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _setup(tmp_path: Path) -> tuple[TestClient, Settings]:
    settings = Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)
    return authed_client(settings), settings


def _start(client: TestClient, name: str, data: bytes, **extra):  # type: ignore[no-untyped-def]
    body = {"filename": name, "size": len(data), "content_hash": _digest(data), **extra}
    return client.post("/api/v1/uploads", json=body)


def _patch(client: TestClient, upload_id: str, data: bytes, offset: int):  # type: ignore[no-untyped-def]
    return client.patch(
        f"/api/v1/uploads/{upload_id}",
        content=data,
        headers={"Upload-Offset": str(offset), "Content-Type": OCTETS},
    )


def test_whole_file_in_one_request_lands_in_uploads_year(tmp_path: Path) -> None:
    client, settings = _setup(tmp_path)
    data = _jpeg(10, "2021:06:15 12:30:00")
    made = _start(client, "IMG_1.jpg", data)
    assert made.status_code == 201
    body = made.json()
    assert body["state"] == "open" and body["offset"] == 0 and body["size"] == len(data)
    done = _patch(client, body["id"], data, 0)
    assert done.status_code == 200
    out = done.json()
    assert out["state"] == "done"
    assert out["asset"]["relative_path"] == "Uploads/2021/IMG_1.jpg"
    assert out["asset"]["taken_at"].startswith("2021-06-15")
    assert (library_dir(settings, client) / "Uploads" / "2021" / "IMG_1.jpg").read_bytes() == data
    listed = client.get("/api/v1/assets").json()["items"]
    assert [row["id"] for row in listed] == [out["asset_id"]]
    assert client.get(f"/api/v1/uploads/{body['id']}").status_code == 404  # finished uploads are gone


def test_resume_after_a_break_and_wrong_offset(tmp_path: Path) -> None:
    client, _ = _setup(tmp_path)
    data = _jpeg(20, "2019:01:02 03:04:05")
    upload = _start(client, "a.jpg", data).json()["id"]
    half = len(data) // 2
    first = _patch(client, upload, data[:half], 0)
    assert first.status_code == 200 and first.json()["offset"] == half
    assert first.headers["Upload-Offset"] == str(half)

    head = client.head(f"/api/v1/uploads/{upload}")
    assert head.status_code == 204
    assert head.headers["Upload-Offset"] == str(half) and head.headers["Upload-Length"] == str(len(data))

    wrong = _patch(client, upload, data[half:], 0)
    assert wrong.status_code == 409
    assert wrong.json()["code"] == "upload_offset_mismatch"
    assert wrong.json()["params"]["offset"] == half

    last = _patch(client, upload, data[half:], half)
    assert last.json()["state"] == "done"


def test_create_is_idempotent_per_hash(tmp_path: Path) -> None:
    client, _ = _setup(tmp_path)
    data = _jpeg(30)
    first = _start(client, "a.jpg", data)
    _patch(client, first.json()["id"], data[:10], 0)
    again = _start(client, "renamed.jpg", data)
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"] and again.json()["offset"] == 10


def test_hash_mismatch_discards_the_upload(tmp_path: Path) -> None:
    client, _ = _setup(tmp_path)
    data = _jpeg(40)
    wrong = bytearray(data)
    wrong[-3] ^= 0xFF
    upload = _start(client, "a.jpg", data).json()["id"]
    bad = _patch(client, upload, bytes(wrong), 0)
    assert bad.status_code == 422 and bad.json()["code"] == "upload_hash_mismatch"
    assert client.head(f"/api/v1/uploads/{upload}").status_code == 404
    assert client.get("/api/v1/assets").json()["items"] == []


def test_a_photo_the_library_has_is_not_uploaded_again(tmp_path: Path) -> None:
    client, _ = _setup(tmp_path)
    data = _jpeg(50)
    upload = _start(client, "a.jpg", data).json()["id"]
    asset_id = _patch(client, upload, data, 0).json()["asset_id"]

    checked = client.post("/api/v1/uploads/check", json={"items": [{"content_hash": _digest(data), "size": len(data)}]})
    assert checked.json() == {"known": [_digest(data)], "removed": []}
    again = _start(client, "copy.jpg", data)
    assert again.status_code == 200
    assert again.json()["state"] == "exists" and again.json()["asset_id"] == asset_id
    assert len(client.get("/api/v1/assets").json()["items"]) == 1


def test_deleted_photo_is_reported_removed_and_can_still_be_sent_on_purpose(tmp_path: Path) -> None:
    client, _ = _setup(tmp_path)
    data = _jpeg(60)
    upload = _start(client, "a.jpg", data).json()["id"]
    asset_id = _patch(client, upload, data, 0).json()["asset_id"]
    assert client.delete(f"/api/v1/assets/{asset_id}").status_code == 200

    other = _jpeg(61)
    checked = client.post(
        "/api/v1/uploads/check",
        json={"items": [{"content_hash": _digest(data), "size": 1}, {"content_hash": _digest(other), "size": 1}]},
    ).json()
    assert checked == {"known": [], "removed": [_digest(data)]}

    # the server does not forbid it: the app decides not to send removed hashes
    redo = _start(client, "a.jpg", data)
    assert redo.status_code == 201
    assert _patch(client, redo.json()["id"], data, 0).json()["state"] == "done"


def test_too_many_bytes_changes_nothing(tmp_path: Path) -> None:
    client, _ = _setup(tmp_path)
    data = _jpeg(70)
    upload = _start(client, "a.jpg", data).json()["id"]
    _patch(client, upload, data[:5], 0)
    over = _patch(client, upload, data, 5)
    assert over.status_code == 400 and over.json()["code"] == "upload_too_long"
    assert client.get(f"/api/v1/uploads/{upload}").json()["offset"] == 5


def test_missing_offset_header(tmp_path: Path) -> None:
    client, _ = _setup(tmp_path)
    data = _jpeg(71)
    upload = _start(client, "a.jpg", data).json()["id"]
    res = client.patch(f"/api/v1/uploads/{upload}", content=data, headers={"Content-Type": OCTETS})
    assert res.status_code == 400 and res.json()["code"] == "upload_offset_missing"


def test_refusals_at_create(tmp_path: Path) -> None:
    client, _ = _setup(tmp_path)
    text = b"hello"
    assert _start(client, "notes.txt", text).status_code == 415
    bad = client.post("/api/v1/uploads", json={"filename": "a.jpg", "size": 3, "content_hash": "xyz"})
    assert bad.status_code == 400 and bad.json()["code"] == "invalid_hash"
    huge = client.post(
        "/api/v1/uploads",
        json={"filename": "a.mp4", "size": uploads.MAX_FILE_BYTES + 1, "content_hash": "a" * 64},
    )
    assert huge.status_code == 413 and huge.json()["code"] == "upload_too_large"


def test_open_upload_limit(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client, _ = _setup(tmp_path)
    monkeypatch.setattr(uploads, "MAX_OPEN_PER_USER", 2)
    assert _start(client, "a.jpg", _jpeg(1)).status_code == 201
    assert _start(client, "b.jpg", _jpeg(2)).status_code == 201
    third = _start(client, "c.jpg", _jpeg(3))
    assert third.status_code == 429 and third.json()["code"] == "upload_limit"


def test_abort_removes_the_upload(tmp_path: Path) -> None:
    client, settings = _setup(tmp_path)
    data = _jpeg(80)
    upload = _start(client, "a.jpg", data).json()["id"]
    _patch(client, upload, data[:7], 0)
    assert client.delete(f"/api/v1/uploads/{upload}").status_code == 204
    assert client.get(f"/api/v1/uploads/{upload}").status_code == 404
    assert not list(uploads.uploads_root(settings).rglob("*.part"))


def test_other_users_cannot_see_an_upload_and_anonymous_is_refused(tmp_path: Path) -> None:
    client, _ = _setup(tmp_path)
    data = _jpeg(90)
    upload = _start(client, "a.jpg", data).json()["id"]
    created = client.put(
        "/api/v1/users", json={"username": "mia", "password": "a-long-enough-password", "role": "member"}
    )
    assert created.status_code in (200, 201)
    other = TestClient(client.app)
    assert other.post("/api/v1/auth/login", json={"username": "mia", "password": "a-long-enough-password"}).status_code == 200
    assert other.get(f"/api/v1/uploads/{upload}").status_code == 404
    assert _patch(other, upload, data, 0).status_code == 404
    anon = TestClient(client.app)
    assert anon.get(f"/api/v1/uploads/{upload}").status_code == 401
    assert anon.post("/api/v1/uploads/check", json={"items": []}).status_code == 401


def test_same_name_gets_a_free_name_and_client_time_is_the_fallback(tmp_path: Path) -> None:
    client, settings = _setup(tmp_path)
    one, two = _png(1), _png(2)
    when = "2018-03-04T05:06:07Z"
    for data in (one, two):
        upload = _start(client, "shot.png", data, taken_at=when).json()["id"]
        out = _patch(client, upload, data, 0).json()
        assert out["state"] == "done"
        assert out["asset"]["taken_at"].startswith("2018-03-04T05:06:07")
    folder = library_dir(settings, client) / "Uploads" / "2018"
    assert sorted(p.name for p in folder.iterdir()) == ["shot-1.png", "shot.png"]


def test_stale_uploads_are_cleaned(tmp_path: Path) -> None:
    client, settings = _setup(tmp_path)
    data = _jpeg(95)
    upload = _start(client, "a.jpg", data).json()["id"]
    folder = next(p for p in uploads.uploads_root(settings).iterdir() if p.is_dir())
    meta_path = folder / f"{upload}.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    old = datetime.now(timezone.utc) - timedelta(days=8)
    meta["touched_at"] = old.isoformat().replace("+00:00", "Z")
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    uploads.remove_stale_all(settings)
    assert not list(folder.iterdir())
    assert client.get(f"/api/v1/uploads/{upload}").status_code == 404
