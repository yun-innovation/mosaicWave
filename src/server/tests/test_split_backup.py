"""Split backups: a set of zips for a library or site too big for one file (doc/backup.md)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from authutil import authed_client, library_dir, me_id
from fastapi.testclient import TestClient
from test_backup import _seed, _settings
from test_cancel import _until
from test_online_restore import _lock, _stage

from mosaicwave import libbackup, restore
from mosaicwave.archive import (
    ArchiveError,
    PartsMissing,
    open_set,
    part_name,
    plan_parts,
)
from mosaicwave.backup import backup_site
from mosaicwave.restore import check_archive, restore_site, stage_site


def _entries(archive: Path) -> list[str]:
    with zipfile.ZipFile(archive) as zf:
        return [n for n in zf.namelist() if n != "manifest.json"]


def _manifest(archive: Path) -> dict:
    with zipfile.ZipFile(archive) as zf:
        return json.loads(zf.read("manifest.json"))


def _files(count: int, size: int) -> list[tuple[Path, str, int]]:
    return [(Path(f"f{i}"), f"libraries/u/f{i}", size) for i in range(count)]


# --- the plan ------------------------------------------------------------------------------------


def test_plan_without_a_limit_is_one_part() -> None:
    assert len(plan_parts(_files(5, 100), None)) == 1


def test_plan_fills_parts_up_to_the_limit() -> None:
    groups = plan_parts(_files(5, 100), 250)
    assert [len(g) for g in groups] == [2, 2, 1]


def test_plan_gives_a_file_bigger_than_the_limit_a_part_of_its_own() -> None:
    files = _files(2, 10) + [(Path("big"), "libraries/u/big", 1000)] + _files(1, 10)
    groups = plan_parts(files, 100)
    assert [len(g) for g in groups] == [2, 1, 1]
    assert groups[1][0][1].endswith("big")


def test_plan_counts_what_the_first_part_already_holds() -> None:
    groups = plan_parts(_files(2, 100), 150, first_extra=100)
    assert [len(g) for g in groups] == [0, 1, 1]  # the databases fill part 1


def test_plan_refuses_a_size_that_needs_too_many_parts() -> None:
    with pytest.raises(ArchiveError, match="at most"):
        plan_parts(_files(1000, 10), 1)


# --- a site backup in parts ----------------------------------------------------------------------


def test_site_backup_in_parts_restores_everything(tmp_path: Path) -> None:
    src = _settings(tmp_path / "a")
    client = _seed(src)
    uid = me_id(client)
    result = backup_site(src, tmp_path / "out", split_bytes=1)

    assert len(result.paths) == 3 and result.path == result.paths[0]
    base = result.path.name[: -len("-001.zip")]
    assert [p.name for p in result.paths] == [part_name(base, n) for n in (1, 2, 3)]
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == sorted(p.name for p in result.paths)

    first = _entries(result.paths[0])
    assert {"library.db", f"accounts/{uid}/library.db"} <= set(first)
    for part in result.paths[1:]:
        names = _entries(part)
        assert len(names) == 1 and names[0].startswith(f"libraries/{uid}/")
    for number, part in enumerate(result.paths, start=1):
        m = _manifest(part)
        assert (m["format"], m["part"], m["parts"]) == (2, number, 3)
    assert result.manifest["parts"] == 3 and result.manifest["file_count"] == 2
    assert result.manifest["assets"] == 2 and "part" not in result.manifest

    dst = _settings(tmp_path / "b")
    restore_site(result.path, dst)
    restored = authed_client(dst)
    assert len(restored.get("/api/v1/assets").json()["items"]) == 2
    assert [(a["name"], a["asset_count"]) for a in restored.get("/api/v1/albums").json()] == [("Holiday", 1)]
    assert (dst.data_dir / "libraries" / uid / "sub" / "blue.jpg").is_file()
    client.close()


def test_restoring_from_any_part_finds_the_others(tmp_path: Path) -> None:
    src = _settings(tmp_path / "a")
    client = _seed(src)
    result = backup_site(src, tmp_path / "out", split_bytes=1)
    found = open_set(result.paths[2], lambda m: None)
    assert found.paths == result.paths
    assert found.manifest["file_count"] == 2 and found.manifest["parts"] == 3
    assert check_archive(result.paths[1], full=True)["file_count"] == 2
    client.close()


def test_a_missing_part_is_named_and_nothing_is_staged(tmp_path: Path) -> None:
    src = _settings(tmp_path / "a")
    client = _seed(src)
    result = backup_site(src, tmp_path / "out", split_bytes=1)
    result.paths[1].unlink()

    with pytest.raises(PartsMissing) as caught:
        check_archive(result.path)
    assert caught.value.missing == [2] and caught.value.parts == 3

    dst = _settings(tmp_path / "b")
    with pytest.raises(PartsMissing):
        stage_site(result.path, dst)
    assert not list(dst.data_dir.glob(".restore-staging-*"))
    # one part on its own is still a valid upload; the set is judged when it is restored
    assert check_archive(result.paths[2], part_only=True)["part"] == 3
    client.close()


def test_a_part_from_another_backup_is_refused(tmp_path: Path) -> None:
    src = _settings(tmp_path / "a")
    client = _seed(src)
    one = backup_site(src, tmp_path / "one", split_bytes=1)
    two = backup_site(src, tmp_path / "two", split_bytes=1)
    # same names, different backup: pretend the clock was the same second
    manifest = json.loads(zipfile.ZipFile(two.paths[1]).read("manifest.json"))
    manifest["created_at"] = "2020-01-01T00:00:00+00:00"
    swapped = two.paths[1].with_suffix(".tmp")
    with zipfile.ZipFile(two.paths[1]) as zin, zipfile.ZipFile(swapped, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            zout.writestr(info, json.dumps(manifest) if info.filename == "manifest.json" else data)
    swapped.replace(one.paths[1])
    with pytest.raises(ArchiveError, match="part 2 belongs to a different backup"):
        check_archive(one.path)
    client.close()


def test_a_part_must_keep_its_name(tmp_path: Path) -> None:
    src = _settings(tmp_path / "a")
    client = _seed(src)
    result = backup_site(src, tmp_path / "out", split_bytes=1)
    renamed = result.paths[1].with_name("my backup.zip")
    result.paths[1].rename(renamed)
    with pytest.raises(ArchiveError, match="keep its file name"):
        check_archive(renamed)
    client.close()


def test_a_damaged_part_fails_the_full_check(tmp_path: Path) -> None:
    src = _settings(tmp_path / "a")
    client = _seed(src)
    result = backup_site(src, tmp_path / "out", split_bytes=1)
    data = bytearray(result.paths[2].read_bytes())
    data[len(data) // 4] ^= 0xFF
    result.paths[2].write_bytes(bytes(data))
    with pytest.raises(ArchiveError):
        check_archive(result.path, full=True)
    client.close()


def test_a_database_in_a_later_part_is_refused(tmp_path: Path) -> None:
    src = _settings(tmp_path / "a")
    client = _seed(src)
    result = backup_site(src, tmp_path / "out", split_bytes=1)
    with zipfile.ZipFile(result.paths[1], "a") as zf:
        zf.writestr("library.db", b"x")
    with pytest.raises(ArchiveError, match="only the first part"):
        check_archive(result.path)
    client.close()


def test_a_split_that_fits_in_one_zip_is_an_ordinary_archive(tmp_path: Path) -> None:
    src = _settings(tmp_path / "a")
    client = _seed(src)
    result = backup_site(src, tmp_path / "out", split_bytes=1 << 30)
    assert len(result.paths) == 1 and not result.path.name[: -len(".zip")].endswith("-001")
    m = _manifest(result.path)
    assert m["format"] == 1 and "parts" not in m
    client.close()


def test_a_cancel_in_the_middle_leaves_no_part_behind(tmp_path: Path) -> None:
    src = _settings(tmp_path / "a")
    client = _seed(src)
    seen: list[int] = []

    def stop(done: int, total: int, _name: str) -> None:
        seen.append(done)
        if done == 2:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        backup_site(src, tmp_path / "out", split_bytes=1, on_progress=stop)
    assert list((tmp_path / "out").iterdir()) == []
    client.close()


# --- a library backup in parts -------------------------------------------------------------------


def test_library_backup_in_parts_restores_into_another_account(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    admin = _seed(settings)
    uid = me_id(admin)
    out = tmp_path / "lib-out"
    result = libbackup.backup_library(settings, uid, out, split_bytes=1)
    assert len(result.paths) == 3
    assert _entries(result.paths[0]) == ["library.db"]
    assert result.manifest["file_count"] == 2 and result.manifest["parts"] == 3

    with pytest.raises(PartsMissing):
        # not every part is next to it
        folder = tmp_path / "partial"
        folder.mkdir()
        (folder / result.paths[0].name).write_bytes(result.paths[0].read_bytes())
        libbackup.check_library_archive(folder / result.paths[0].name)

    assert libbackup.check_library_archive(result.path, full=True)["assets"] == 2
    staged = libbackup.stage_library(result.paths[2], settings, uid)
    try:
        files = [p for p in (staged.data_stage / "libraries" / uid).rglob("*") if p.is_file()]
        assert sorted(p.name for p in files) == ["blue.jpg", "red.jpg"]
        assert (staged.db_stage / "library.db").is_file()
    finally:
        import shutil

        shutil.rmtree(staged.db_stage, ignore_errors=True)
        shutil.rmtree(staged.data_stage, ignore_errors=True)
    assert library_dir(settings, admin).is_dir()


# --- the API: listing, download, upload, delete and restore of a set ---------------------------------


def _big_photos(settings, client) -> None:  # type: ignore[no-untyped-def]
    """Two photos of about 120 KB each, so a 0.0001 GB (about 107 KB) part holds one."""
    from authutil import library_dir, scan_library
    from test_takeout import _jpeg_bytes

    folder = library_dir(settings, client)
    for name, colour in (("big1.jpg", (9, 9, 200)), ("big2.jpg", (9, 200, 9))):
        (folder / name).write_bytes(_jpeg_bytes(colour) + b"\0" * 120_000)
    scan_library(client)


def _split_backup(client: TestClient) -> dict:
    res = client.post("/api/v1/backups", json={"split_gb": 0.0001})
    assert res.status_code == 200, res.text
    job = _until(client, res.json()["id"], {"done", "error", "cancelled"})
    assert job["status"] == "done", job
    listed = client.get("/api/v1/backups").json()
    assert len(listed) == 1
    return listed[0]


def test_a_split_backup_is_one_entry_with_its_parts(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    _big_photos(settings, client)
    entry = _split_backup(client)

    assert entry["parts"] >= 3 and entry["missing"] == []
    assert entry["name"].endswith("-001.zip")
    assert [p["name"] for p in entry["part_files"]][0] == entry["name"]
    assert entry["size"] == sum(p["size"] for p in entry["part_files"])
    assert (entry["assets"], entry["file_count"]) == (4, 4)
    for part in entry["part_files"]:
        got = client.get(f"/api/v1/backups/{part['name']}")
        assert got.status_code == 200 and len(got.content) == part["size"]

    # an ordinary backup next to it stays a single entry
    single = client.post("/api/v1/backups")
    _until(client, single.json()["id"], {"done"})
    listed = client.get("/api/v1/backups").json()
    assert sorted(e["parts"] > 1 for e in listed) == [False, True]


def test_restore_of_a_split_backup_by_name_and_from_a_path(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    _big_photos(settings, client)
    entry = _split_backup(client)
    _lock(client)

    assert _stage(client, entry["name"])["status"] == "done"
    pending = client.get("/api/v1/backups/restore").json()["pending"]
    assert pending["archive"] == entry["name"] and pending["summary"]["assets"] == 4
    client.delete("/api/v1/backups/restore")

    last = settings.backup_dir / entry["part_files"][-1]["name"]
    res = client.post("/api/v1/backups/restore", json={"path": str(last)})
    assert res.status_code == 200, res.text
    assert _until(client, res.json()["id"], {"done", "error"})["status"] == "done"


def test_a_missing_part_blocks_the_restore_and_is_listed(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    _big_photos(settings, client)
    entry = _split_backup(client)
    (settings.backup_dir / entry["part_files"][1]["name"]).unlink()
    _lock(client)

    listed = client.get("/api/v1/backups").json()[0]
    assert listed["missing"] == [2] and len(listed["part_files"]) == entry["parts"] - 1
    refused = client.post(f"/api/v1/backups/{entry['name']}/restore")
    assert refused.status_code == 400 and refused.json()["code"] == "backup_parts_missing"
    assert refused.json()["params"] == {"missing": "2", "parts": entry["parts"]}
    assert not restore.pending_exists(settings)


def test_uploading_the_parts_one_by_one_rebuilds_the_set(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    _big_photos(settings, client)
    entry = _split_backup(client)
    originals = {p["name"]: (settings.backup_dir / p["name"]).read_bytes() for p in entry["part_files"]}
    client.delete(f"/api/v1/backups/{entry['name']}")
    assert client.get("/api/v1/backups").json() == []
    _lock(client)

    names = list(originals)
    for number, name in enumerate(reversed(names), start=1):  # in any order
        up = client.put("/api/v1/backups/upload", content=originals[name])
        assert up.status_code == 200, up.text
        body = up.json()
        assert name in [p["name"] for p in body["part_files"]]
        assert len(body["missing"]) == len(names) - number
    assert client.put("/api/v1/backups/upload", content=originals[names[1]]).status_code == 200  # same part again
    final = client.get("/api/v1/backups").json()
    assert len(final) == 1 and final[0]["missing"] == [] and final[0]["name"] == entry["name"]
    assert _stage(client, final[0]["name"])["status"] == "done"


def test_deleting_a_split_backup_removes_every_part(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    _big_photos(settings, client)
    entry = _split_backup(client)
    assert client.delete(f"/api/v1/backups/{entry['part_files'][2]['name']}").json() == {"ok": True}
    assert client.get("/api/v1/backups").json() == []
    assert not list(settings.backup_dir.glob("*.zip"))


def test_a_library_backup_in_parts_through_the_api(tmp_path: Path) -> None:
    from authutil import me_id

    settings = _settings(tmp_path)
    client = _seed(settings)
    _big_photos(settings, client)
    res = client.post("/api/v1/me/backups", json={"split_gb": 0.0001})
    assert res.status_code == 200, res.text
    assert _until(client, res.json()["id"], {"done", "error"})["status"] == "done"
    listed = client.get("/api/v1/me/backups").json()
    assert len(listed) == 1 and listed[0]["parts"] >= 3 and listed[0]["missing"] == []
    assert listed[0]["name"].startswith("mosaicwave-library-") and listed[0]["name"].endswith("-001.zip")

    folder = settings.backup_dir / "users" / me_id(client)
    (folder / listed[0]["part_files"][1]["name"]).unlink()
    refused = client.post(f"/api/v1/me/backups/{listed[0]['name']}/restore")
    assert refused.status_code == 400 and refused.json()["code"] == "backup_parts_missing"