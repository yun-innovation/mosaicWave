"""Export: the user's photos as ordinary files, zip parts and Takeout layout (doc/backup.md)."""

from __future__ import annotations

import json
import os
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from authutil import library_dir, library_session, me_id, scan_library
from test_backup import _make_member, _seed, _settings
from test_cancel import Gate, _events, _until
from test_takeout import _jpeg_bytes, _wait_import

from mosaicwave import export as export_mod
from mosaicwave.archive import ArchiveError
from mosaicwave.config import Settings
from mosaicwave.library import importjob
from mosaicwave.library.dump import inspect_takeout_path
from mosaicwave.library.media import TAKEOUT_ZIP
from mosaicwave.models import Asset

DONE = {"done", "error", "cancelled"}


def _export(client: TestClient, **body) -> dict:  # type: ignore[no-untyped-def]
    res = client.post("/api/v1/me/exports", json=body)
    assert res.status_code == 200, res.text
    return _until(client, res.json()["id"], DONE)


def _latest(client: TestClient) -> dict:
    return client.get("/api/v1/me/exports").json()[0]


def _folder(settings: Settings, client: TestClient, view: dict) -> Path:
    return settings.export_dir / me_id(client) / view["name"]


def _zip_names(folder: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for path in sorted(folder.glob("*.zip")):
        with zipfile.ZipFile(path) as zf:
            out[path.name] = sorted(zf.namelist())
    return out


def _all_names(folder: Path) -> list[str]:
    names = [n for names in _zip_names(folder).values() for n in names]
    for path in folder.rglob("*"):
        if path.is_file() and path.suffix != ".zip" and path.name != export_mod.INFO_NAME:
            names.append(path.relative_to(folder).as_posix())
    return sorted(names)


def _in_album(client: TestClient) -> str:
    album = client.get("/api/v1/albums").json()[0]
    items = client.get("/api/v1/assets", params={"album_id": album["id"]}).json()["items"]
    assert len(items) == 1
    return items[0]["filename"]


def _padded(color: tuple[int, int, int], size: int) -> bytes:
    """A real jpeg followed by zeros, so each file is distinct and as big as the test needs."""
    data = _jpeg_bytes(color)
    return data + b"\0" * max(0, size - len(data))


def _big_library(settings: Settings, client: TestClient) -> None:
    folder = library_dir(settings, client)
    for name, color, size in (
        ("a.jpg", (10, 20, 30), 600_000),
        ("b.jpg", (40, 50, 60), 600_000),
        ("big.jpg", (70, 80, 90), 1_500_000),
        ("c.jpg", (100, 110, 120), 600_000),
    ):
        (folder / name).write_bytes(_padded(color, size))
    scan_library(client)


def _listing(settings: Settings, client: TestClient) -> list[Path]:
    base = settings.export_dir / me_id(client)
    return sorted(base.iterdir()) if base.is_dir() else []


# --- layouts -------------------------------------------------------------------------------------


def test_plain_export_puts_albums_in_folders_and_the_rest_in_photos(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    in_album = _in_album(client)
    other = ({"red.jpg", "blue.jpg"} - {in_album}).pop()

    job = _export(client, all=True)
    assert job["status"] == "done" and job["kind"] == "export", job
    view = _latest(client)
    assert (view["layout"], view["assets"], view["albums"]) == ("plain", 2, 1)
    assert view["split_bytes"] is None and view["warnings"] == []
    assert [f["path"] for f in view["files"]] == [f["path"] for f in view["files"] if f["zipped"]]
    folder = _folder(settings, client, view)
    zips = _zip_names(folder)
    assert len(zips) == 1
    assert re.match(r"^mosaicwave-\d{8}T\d{6}Z-001\.zip$", next(iter(zips)))
    assert sorted(next(iter(zips.values()))) == sorted([f"Holiday/{in_album}", f"Photos/{other}"])
    with zipfile.ZipFile(next(iter(folder.glob("*.zip")))) as zf:
        assert zf.read(f"Holiday/{in_album}") == (
            (library_dir(settings, client) / ("sub" if in_album == "blue.jpg" else ".") / in_album).read_bytes()
        )
    assert (folder / export_mod.INFO_NAME).is_file()
    assert not _leftovers(settings, client)


def _leftovers(settings: Settings, client: TestClient) -> list[Path]:
    return [p for p in _listing(settings, client) if not export_mod.EXPORT_NAME.match(p.name)]


def test_takeout_export_matches_google_takeout(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    in_album = _in_album(client)
    other = ({"red.jpg", "blue.jpg"} - {in_album}).pop()
    _export(client, all=True, layout="takeout")
    view = _latest(client)
    folder = _folder(settings, client, view)
    (zip_path,) = folder.glob("*.zip")
    assert TAKEOUT_ZIP.match(zip_path.name)
    root = "Takeout/Google Photos"
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        year = next(n for n in names if n.endswith(f"/{other}")).split("/")[2]
        assert re.fullmatch(r"Photos from \d{4}", year)
        assert names == {
            f"{root}/Holiday/{in_album}",
            f"{root}/Holiday/{in_album}.json",
            f"{root}/{year}/{other}",
            f"{root}/{year}/{other}.json",
        }
        side = json.loads(zf.read(f"{root}/Holiday/{in_album}.json"))
    assert side["title"] == in_album
    assert side["photoTakenTime"]["timestamp"].isdigit() and side["photoTakenTime"]["formatted"].endswith("UTC")
    assert "geoData" not in side

    report = inspect_takeout_path(folder)
    assert report.ready and report.zip_count == 1


def test_entries_keep_their_dates(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    when = datetime(2020, 5, 6, 7, 8, 10, tzinfo=timezone.utc).timestamp()
    for path in library_dir(settings, client).rglob("*.jpg"):
        os.utime(path, (when, when))
    scan_library(client)
    _export(client, all=True)
    (zip_path,) = _folder(settings, client, _latest(client)).glob("*.zip")
    with zipfile.ZipFile(zip_path) as zf:
        stamps = {i.date_time for i in zf.infolist()}
    assert stamps == {(2020, 5, 6, 7, 8, 10)}


def test_sidecar_carries_location_and_caption() -> None:
    entry = export_mod.Entry(
        asset_id="a",
        folder="x",
        name="p.jpg",
        source_id="s",
        rel="p.jpg",
        size=1,
        when=datetime(2024, 3, 9, 15, 4, 5, tzinfo=timezone.utc),
        extra={"caption": "Kyoto", "geo": {"lat": 35.0, "lng": 135.5, "alt": 12.0}},
        in_album=False,
    )
    data = json.loads(export_mod.sidecar_json(entry))
    assert data["description"] == "Kyoto"
    assert data["photoTakenTime"] == {"timestamp": "1709996645", "formatted": "Mar 9, 2024, 3:04:05 PM UTC"}
    assert (data["geoData"]["latitude"], data["geoData"]["longitude"], data["geoData"]["altitude"]) == (
        35.0,
        135.5,
        12.0,
    )


def test_same_file_name_gets_a_number(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    folder = library_dir(settings, client)
    (folder / "sub" / "red.jpg").write_bytes(_jpeg_bytes((1, 2, 3)))  # also red.jpg, in sub/
    scan_library(client)
    ids = [a["id"] for a in client.get("/api/v1/assets").json()["items"]]
    assert len(ids) == 3

    _export(client, asset_ids=ids)
    names = _all_names(_folder(settings, client, _latest(client)))
    assert [n for n in names if n.startswith("Photos/red")] == ["Photos/red (2).jpg", "Photos/red.jpg"]

    _export(client, asset_ids=ids, layout="takeout")
    names = _all_names(_folder(settings, client, _latest(client)))
    reds = sorted(n.rsplit("/", 1)[1] for n in names if "/red" in n)
    assert reds == ["red(1).jpg", "red(1).jpg.json", "red.jpg", "red.jpg.json"]


# --- splitting -----------------------------------------------------------------------------------


def test_split_parts_and_an_oversized_file_that_is_not_zipped_plain(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    for path in library_dir(settings, client).rglob("*.jpg"):
        path.unlink()
    _big_library(settings, client)
    scan_library(client)

    job = _export(client, all=True, split_gb=0.001)  # about 1 MB per part
    assert job["status"] == "done", job
    view = _latest(client)
    folder = _folder(settings, client, view)
    zips = _zip_names(folder)
    assert sorted(zips) == [f"mosaicwave-{folder.name[7:]}-00{n}.zip" for n in (1, 2, 3)]
    assert [names for names in zips.values()] == [["Photos/a.jpg"], ["Photos/b.jpg"], ["Photos/c.jpg"]]
    # the big file sits beside the zips under the path it would have had inside one
    assert (folder / "Photos" / "big.jpg").stat().st_size == 1_500_000
    assert _all_names(folder) == ["Photos/a.jpg", "Photos/b.jpg", "Photos/big.jpg", "Photos/c.jpg"]
    assert view["assets"] == 4
    files = {f["path"]: f for f in view["files"]}
    assert files["Photos/big.jpg"]["zipped"] is False
    assert all(f["zipped"] for p, f in files.items() if p.endswith(".zip"))

    res = client.get(f"/api/v1/me/exports/{view['name']}/files/Photos/big.jpg")
    assert res.status_code == 200 and len(res.content) == 1_500_000
    part = next(iter(zips))
    assert client.get(f"/api/v1/me/exports/{view['name']}/files/{part}").content == (folder / part).read_bytes()
    log = _events(client, job["id"])
    assert any("too big to zip" in line for line in log)


def test_takeout_oversized_file_sits_beside_the_zips_and_imports_back(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    for path in library_dir(settings, client).rglob("*.jpg"):
        path.unlink()
    _big_library(settings, client)
    scan_library(client)
    items = {a["filename"]: a["id"] for a in client.get("/api/v1/assets").json()["items"]}
    assert set(items) == {"a.jpg", "b.jpg", "big.jpg", "c.jpg"}
    trip = client.post("/api/v1/albums", json={"name": "Trip"}).json()
    ok = client.post(f"/api/v1/albums/{trip['id']}/assets", json={"asset_ids": [items["big.jpg"], items["a.jpg"]]})
    assert ok.status_code == 200, ok.text

    job = _export(client, all=True, layout="takeout", split_gb=0.001)
    assert job["status"] == "done", job
    folder = _folder(settings, client, _latest(client))
    stamp = folder.name[len("export-") :]
    root = "Takeout/Google Photos"
    zips = _zip_names(folder)
    # Google's numbering: the oversized file takes a part number of its own (here 004, after zips 1-3)
    assert sorted(zips) == [f"takeout-{stamp}-{n:03d}.zip" for n in (1, 2, 3)]
    loose = sorted(p.name for p in folder.iterdir() if p.suffix != ".zip" and p.name != export_mod.INFO_NAME)
    assert loose == ["big-004.jpg"]
    assert (folder / "big-004.jpg").stat().st_size == 1_500_000
    # its sidecar stays inside a zip, in the album folder it belongs to
    owners = [z for z, names in zips.items() if f"{root}/Trip/big.jpg.json" in names]
    assert len(owners) == 1 and not any(f"{root}/Trip/big.jpg" in names for names in zips.values())
    assert all(TAKEOUT_ZIP.match(z) for z in zips)

    report = inspect_takeout_path(folder)
    assert report.ready
    assert not [w for w in report.warnings if w.code == "missing_zip_part"], report.warnings

    # ...and mosaicWave's own Takeout import reads the export back, albums and the big file included
    admin = client
    other = _make_member(admin)
    imported = _wait_import(other, str(folder))
    assert imported["status"] == "done", imported
    got = other.get("/api/v1/assets").json()["items"]
    assert sorted(a["filename"] for a in got) == ["a.jpg", "b.jpg", "big.jpg", "c.jpg"]
    albums = {a["name"]: a["asset_count"] for a in other.get("/api/v1/albums").json()}
    assert albums == {"Trip": 2}


def test_one_zip_when_not_split_even_for_a_large_file(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    for path in library_dir(settings, client).rglob("*.jpg"):
        path.unlink()
    _big_library(settings, client)
    _export(client, all=True)
    folder = _folder(settings, client, _latest(client))
    assert len(_zip_names(folder)) == 1
    assert [p for p in folder.iterdir() if p.suffix != ".zip" and p.name != export_mod.INFO_NAME] == []


# --- what is exported ----------------------------------------------------------------------------


def test_export_one_album_or_chosen_photos(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    in_album = _in_album(client)
    other = ({"red.jpg", "blue.jpg"} - {in_album}).pop()
    album = client.get("/api/v1/albums").json()[0]
    items = {a["filename"]: a["id"] for a in client.get("/api/v1/assets").json()["items"]}

    assert _export(client, album_ids=[album["id"]])["status"] == "done"
    assert _all_names(_folder(settings, client, _latest(client))) == [f"Holiday/{in_album}"]

    assert _export(client, asset_ids=[items[other]])["status"] == "done"
    assert _all_names(_folder(settings, client, _latest(client))) == [f"Photos/{other}"]

    # a photo picked on its own that an exported album already holds is not exported twice
    assert _export(client, album_ids=[album["id"]], asset_ids=list(items.values()))["status"] == "done"
    assert _all_names(_folder(settings, client, _latest(client))) == sorted(
        [f"Holiday/{in_album}", f"Photos/{other}"]
    )


def test_export_checks_what_was_asked_for(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    album = client.get("/api/v1/albums").json()[0]

    res = client.post("/api/v1/me/exports", json={})
    assert (res.status_code, res.json()["code"]) == (400, "export_nothing_selected")
    res = client.post("/api/v1/me/exports", json={"all": True, "album_ids": [album["id"]]})
    assert (res.status_code, res.json()["code"]) == (400, "export_scope")
    res = client.post("/api/v1/me/exports", json={"album_ids": ["nope"]})
    assert (res.status_code, res.json()["code"]) == (404, "album_not_found")
    assert client.post("/api/v1/me/exports", json={"all": True, "layout": "zip"}).status_code == 422
    assert client.post("/api/v1/me/exports", json={"all": True, "split_gb": 0}).status_code == 422
    assert client.get("/api/v1/me/exports").json() == []

    empty = _make_member(client)
    res = empty.post("/api/v1/me/exports", json={"all": True})
    assert (res.status_code, res.json()["code"]) == (400, "export_empty")
    # photos that do not exist export nothing: the job says so instead of writing an empty zip
    job = _export(empty, asset_ids=["nope"])
    assert job["status"] == "error" and "nothing to export" in job["error"]
    assert not _listing(settings, empty)


def test_a_missing_original_is_a_warning_not_a_failure(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    (library_dir(settings, client) / "red.jpg").unlink()
    job = _export(client, all=True)
    assert job["status"] == "done", job
    view = _latest(client)
    assert view["assets"] == 1
    assert any("could not be read" in w for w in view["warnings"])
    assert any("could not be read" in line for line in _events(client, job["id"]))


def test_a_deleted_photo_is_not_exported(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    items = client.get("/api/v1/assets").json()["items"]
    gone = items[0]
    session = library_session(client)
    try:
        row = session.get(Asset, gone["id"])
        row.deleted_at = datetime.now(timezone.utc)
        session.commit()
    finally:
        session.close()
    _export(client, all=True)
    names = _all_names(_folder(settings, client, _latest(client)))
    assert len(names) == 1 and not names[0].endswith(gone["filename"])


# --- jobs ----------------------------------------------------------------------------------------


def test_cancel_leaves_no_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    gate = Gate()
    real = importjob.export_library

    def slow(*args, on_progress, **kwargs):  # type: ignore[no-untyped-def]
        def gated(done: int, total: int, rel: str) -> None:
            gate.step()
            on_progress(done, total, rel)

        return real(*args, on_progress=gated, **kwargs)

    monkeypatch.setattr(importjob, "export_library", slow)
    job = client.post("/api/v1/me/exports", json={"all": True}).json()
    assert gate.reached.wait(10)
    assert client.post(f"/api/v1/jobs/{job['id']}/cancel").status_code == 200
    gate.release.set()

    done = _until(client, job["id"], DONE)
    assert done["status"] == "cancelled"
    assert _listing(settings, client) == []  # neither the .part folder nor an export
    assert client.get("/api/v1/me/exports").json() == []


def test_not_enough_space_refuses_before_writing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)

    def full(folder: Path, need: int) -> None:
        raise ArchiveError("not enough free space")

    monkeypatch.setattr(export_mod, "_require_space", full)
    job = _export(client, all=True)
    assert job["status"] == "error" and "free space" in job["error"]
    assert _listing(settings, client) == []


def test_job_progress_has_no_file_names(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    job = _export(client, all=True)
    assert job["current"] is None
    assert (job["processed"], job["total"]) == (2, 2)
    assert "red.jpg" not in json.dumps(job) and "blue.jpg" not in json.dumps(job)


def test_stale_half_built_exports_are_removed_at_start(tmp_path: Path) -> None:
    from mosaicwave.main import create_app

    settings = _settings(tmp_path)
    stale = settings.export_dir / "someone" / ".export-20260101T000000Z.part"
    (stale / "Photos").mkdir(parents=True)
    (stale / "Photos" / "x.jpg").write_bytes(b"x")
    kept = settings.export_dir / "someone" / "export-20260101T000000Z"
    kept.mkdir()
    create_app(settings)
    assert not stale.exists() and kept.is_dir()


# --- privacy and download ------------------------------------------------------------------------


def test_users_only_see_their_own_exports(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    admin = _seed(settings)
    member = _make_member(admin)
    (library_dir(settings, member) / "mine.jpg").write_bytes(_jpeg_bytes((9, 9, 9)))
    scan_library(member)
    assert _export(member, all=True)["status"] == "done"
    name = _latest(member)["name"]
    zip_name = _latest(member)["files"][0]["path"]

    assert admin.get("/api/v1/me/exports").json() == []
    for call in (
        admin.get(f"/api/v1/me/exports/{name}/files/{zip_name}"),
        admin.delete(f"/api/v1/me/exports/{name}"),
    ):
        assert call.status_code == 404 and call.json()["code"] == "export_not_found"
    assert (settings.export_dir / me_id(member) / name).is_dir()

    # the admin's folder browser does not list the member's export folders either
    parent = str(settings.export_dir)
    listing = admin.get("/api/v1/fs/list", params={"path": parent, "kind": "backup"})
    assert listing.status_code == 200 and listing.json()["entries"] == []
    inside = admin.get("/api/v1/fs/list", params={"path": str(settings.export_dir / me_id(member)), "kind": "backup"})
    assert inside.status_code == 403 and inside.json()["code"] == "folder_not_allowed"
    refused = admin.post("/api/v1/me/backups/restore", json={"path": str(settings.export_dir / me_id(member) / name / zip_name)})
    assert refused.status_code == 403 and refused.json()["code"] == "folder_not_allowed"

    assert member.delete(f"/api/v1/me/exports/{name}").json() == {"ok": True}
    assert member.get("/api/v1/me/exports").json() == []
    assert not (settings.export_dir / me_id(member) / name).exists()


def test_download_refuses_paths_outside_the_export(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    _export(client, all=True)
    view = _latest(client)
    base = f"/api/v1/me/exports/{view['name']}/files"
    zip_name = view["files"][0]["path"]
    assert client.get(f"{base}/{zip_name}").status_code == 200
    for bad in (export_mod.INFO_NAME, "nope.zip", "%2e%2e/%2e%2e/library.db", "..%5Cx", "Photos"):
        assert client.get(f"{base}/{bad}").status_code == 404, bad
    assert client.get("/api/v1/me/exports/not-an-export/files/x.zip").status_code == 404
    assert client.get(f"/api/v1/me/exports/export-20200101T000000Z/files/{zip_name}").status_code == 404


def test_export_is_not_part_of_a_library_backup(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    _export(client, all=True)
    res = client.post("/api/v1/me/backups")
    assert res.status_code == 200, res.text
    assert _until(client, res.json()["id"], DONE)["status"] == "done"
    assert client.get("/api/v1/me/exports").json() != []
    (archive,) = (settings.backup_dir / "users" / me_id(client)).glob("*.zip")
    with zipfile.ZipFile(archive) as zf:
        assert not [n for n in zf.namelist() if "export" in n.lower()]
