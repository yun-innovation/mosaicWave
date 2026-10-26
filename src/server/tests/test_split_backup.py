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
    _overhead,
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


def _limit(settings) -> int:  # type: ignore[no-untyped-def]
    """A part size a little under the biggest database, so that one is too big for a zip."""
    biggest = max(p.stat().st_size for p in settings.data_dir.rglob("library.db"))
    return int(0.85 * biggest / 0.9)


def _pad(settings, client) -> None:  # type: ignore[no-untyped-def]
    """A 30 KB file in the library: with the databases it needs a second zip."""
    (library_dir(settings, client) / "pad.bin").write_bytes(bytes(range(256)) * 120)


def _zips(paths: list[Path]) -> list[Path]:
    return [p for p in paths if p.suffix == ".zip"]


def _bigs(paths: list[Path]) -> list[Path]:
    return [p for p in paths if p.suffix == ".big"]


def _names(paths: list[Path]) -> list[str]:
    """Every name in the backup: the entries of the zips and the big files the manifest lists."""
    zips = _zips(paths)
    names = [n for p in zips for n in _entries(p)]
    return names + [e["name"] for e in _manifest(zips[0]).get("loose", [])]


def _files(count: int, size: int) -> list[tuple[Path, str, int]]:
    return [(Path(f"f{i}"), f"libraries/u/f{i}", size) for i in range(count)]


# --- the plan ------------------------------------------------------------------------------------


def test_plan_without_a_limit_is_one_part() -> None:
    plan = plan_parts(_files(5, 100), None)
    assert len(plan.groups) == 1 and plan.loose == []


def test_plan_fills_parts_up_to_the_limit() -> None:
    # each file costs 100 bytes of data and about 230 of headers; a part holds 900 minus a reserve
    plan = plan_parts(_files(5, 100), 1000)
    assert [len(g) for g in plan.groups] == [2, 2, 1] and plan.loose == []


def test_plan_leaves_a_file_bigger_than_a_part_beside_the_zips() -> None:
    files = _files(1, 10) + [(Path("big"), "libraries/u/big", 5000)] + _files(1, 10)
    plan = plan_parts(files, 1000)
    assert plan.loose == [(Path("big"), "libraries/u/big", 5000)]
    # the small files around it are zipped, and every zip fits
    assert [p.arcname for g in plan.groups for p in g] == ["libraries/u/f0", "libraries/u/f0"]
    for group in plan.groups:
        assert sum(10 + _overhead(p.arcname) for p in group) <= 1000


def test_plan_leaves_a_big_database_beside_the_zips_too() -> None:
    plan = plan_parts([(Path("db"), "library.db", 4000)] + _files(2, 100), 1000)
    assert plan.loose == [(Path("db"), "library.db", 4000)]
    assert sum(len(g) for g in plan.groups) == 2


def test_plan_compresses_a_database_that_fits_whole() -> None:
    plan = plan_parts([(Path("db"), "library.db", 100)] + _files(1, 100), 1000)
    assert [(p.arcname, p.deflate) for p in plan.groups[0]] == [("library.db", True), ("libraries/u/f0", False)]


def test_plan_refuses_a_size_that_needs_too_many_parts() -> None:
    with pytest.raises(ArchiveError, match="at most"):
        plan_parts(_files(1000, 10), 400)


# --- a site backup in parts ----------------------------------------------------------------------


def test_site_backup_in_parts_restores_everything(tmp_path: Path) -> None:
    src = _settings(tmp_path / "a")
    client = _seed(src)
    _pad(src, client)
    uid = me_id(client)
    result = backup_site(src, tmp_path / "out", split_bytes=_limit(src))

    count = len(result.paths)
    zips, bigs = _zips(result.paths), _bigs(result.paths)
    assert len(zips) >= 2 and len(bigs) == 1 and count == len(zips) + len(bigs)
    assert result.path == result.paths[0]
    base = result.path.name[: -len("-001.zip")]
    assert [p.name for p in result.paths] == [
        part_name(base, n, loose=n > len(zips)) for n in range(1, count + 1)
    ]
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == sorted(p.name for p in result.paths)

    limit = _limit(src)
    assert all(p.stat().st_size <= limit for p in zips)  # a zip is never bigger than the size
    names = _names(result.paths)
    assert {"library.db", f"accounts/{uid}/library.db"} <= set(names)
    assert sum(n.startswith(f"libraries/{uid}/") for n in names) == 3
    # the database that is bigger than a part is a plain file beside the zips, not cut and not zipped
    assert bigs[0].name == part_name(base, count, loose=True)
    assert bigs[0].read_bytes()[:16] == b"SQLite format 3\x00"
    assert not any(".mwchunk-" in n for n in names)
    for number, part in enumerate(zips, start=1):
        m = _manifest(part)
        assert (m["format"], m["part"], m["parts"]) == (2, number, count)
        assert [(e["part"], e["name"]) for e in m["loose"]] == [(count, f"accounts/{uid}/library.db")]
    assert result.manifest["parts"] == count and result.manifest["file_count"] == 3
    assert result.manifest["assets"] == 2 and "part" not in result.manifest

    dst = _settings(tmp_path / "b")
    restore_site(result.path, dst)
    assert (dst.database_path.parent / "accounts" / uid / "library.db").stat().st_size > 0
    restored = authed_client(dst)
    assert len(restored.get("/api/v1/assets").json()["items"]) == 2
    assert [(a["name"], a["asset_count"]) for a in restored.get("/api/v1/albums").json()] == [("Holiday", 1)]
    assert (dst.data_dir / "libraries" / uid / "sub" / "blue.jpg").is_file()
    assert (dst.data_dir / "libraries" / uid / "pad.bin").read_bytes() == bytes(range(256)) * 120
    client.close()


def test_restoring_from_any_part_finds_the_others(tmp_path: Path) -> None:
    src = _settings(tmp_path / "a")
    client = _seed(src)
    _pad(src, client)
    result = backup_site(src, tmp_path / "out", split_bytes=_limit(src))
    found = open_set(result.paths[-1], lambda m: None)  # the big file works as a way in too
    assert found.paths == _zips(result.paths) and [b.path for b in found.loose] == _bigs(result.paths)
    assert found.manifest["file_count"] == 3 and found.manifest["parts"] == len(result.paths)
    assert check_archive(result.paths[1], full=True)["file_count"] == 3
    client.close()


def test_a_missing_part_is_named_and_nothing_is_staged(tmp_path: Path) -> None:
    src = _settings(tmp_path / "a")
    client = _seed(src)
    _pad(src, client)
    result = backup_site(src, tmp_path / "out", split_bytes=_limit(src))
    big = _bigs(result.paths)[0]
    result.paths[1].unlink()

    with pytest.raises(PartsMissing) as caught:
        check_archive(result.path)
    assert caught.value.missing == [2] and caught.value.parts == len(result.paths)
    big.unlink()  # a big file is a part like the others
    with pytest.raises(PartsMissing) as caught:
        check_archive(result.path)
    assert caught.value.missing == [2, 3]

    dst = _settings(tmp_path / "b")
    with pytest.raises(PartsMissing):
        stage_site(result.path, dst)
    assert not list(dst.data_dir.glob(".restore-staging-*"))
    # one part on its own is still a valid upload; the set is judged when it is restored
    assert check_archive(result.paths[0], part_only=True)["part"] == 1
    client.close()


def test_a_part_from_another_backup_is_refused(tmp_path: Path) -> None:
    src = _settings(tmp_path / "a")
    client = _seed(src)
    _pad(src, client)
    one = backup_site(src, tmp_path / "one", split_bytes=_limit(src))
    two = backup_site(src, tmp_path / "two", split_bytes=_limit(src))
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
    _pad(src, client)
    result = backup_site(src, tmp_path / "out", split_bytes=_limit(src))
    renamed = result.paths[1].with_name("my backup.zip")
    result.paths[1].rename(renamed)
    with pytest.raises(ArchiveError, match="keep its file name"):
        check_archive(renamed)
    client.close()


def test_a_damaged_part_fails_the_full_check(tmp_path: Path) -> None:
    src = _settings(tmp_path / "a")
    client = _seed(src)
    _pad(src, client)
    result = backup_site(src, tmp_path / "out", split_bytes=_limit(src))
    assert check_archive(result.path, full=True)["file_count"] == 3
    # a zip fails its CRC
    zip2 = result.paths[1]
    original = zip2.read_bytes()
    data = bytearray(original)
    data[len(data) // 4] ^= 0xFF
    zip2.write_bytes(bytes(data))
    with pytest.raises(ArchiveError):
        check_archive(result.path, full=True)
    zip2.write_bytes(original)
    # a big file fails its checksum (same size, so only the full check sees it) and a short one at once
    big = _bigs(result.paths)[0]
    original = big.read_bytes()
    data = bytearray(original)
    data[len(data) // 2] ^= 0xFF
    big.write_bytes(bytes(data))
    check_archive(result.path)
    with pytest.raises(ArchiveError, match="does not match"):
        check_archive(result.path, full=True)
    with pytest.raises(ArchiveError, match="does not match"):
        stage_site(result.path, _settings(tmp_path / "b"))
    big.write_bytes(original[:-1])
    with pytest.raises(ArchiveError, match="not the size"):
        check_archive(result.path)
    client.close()


def test_a_second_copy_of_a_database_is_refused(tmp_path: Path) -> None:
    src = _settings(tmp_path / "a")
    client = _seed(src)
    _pad(src, client)
    result = backup_site(src, tmp_path / "out", split_bytes=_limit(src))
    with zipfile.ZipFile(result.paths[1], "a") as zf:
        zf.writestr("library.db", b"x")
    with pytest.raises(ArchiveError, match="in two parts"):
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
        backup_site(src, tmp_path / "out", split_bytes=_limit(src), on_progress=stop)
    assert list((tmp_path / "out").iterdir()) == []
    client.close()


# --- a library backup in parts -------------------------------------------------------------------


def test_library_backup_in_parts_restores_into_another_account(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    admin = _seed(settings)
    uid = me_id(admin)
    out = tmp_path / "lib-out"
    result = libbackup.backup_library(settings, uid, out, split_bytes=_limit(settings))
    count = len(result.paths)
    assert count >= 2
    assert all(p.stat().st_size <= _limit(settings) for p in _zips(result.paths))
    assert sorted(_names(result.paths)) == [
        f"libraries/{uid}/red.jpg",
        f"libraries/{uid}/sub/blue.jpg",
        "library.db",
    ]
    assert result.manifest["file_count"] == 2 and result.manifest["parts"] == count

    with pytest.raises(PartsMissing):
        # not every part is next to it
        folder = tmp_path / "partial"
        folder.mkdir()
        (folder / result.paths[0].name).write_bytes(result.paths[0].read_bytes())
        libbackup.check_library_archive(folder / result.paths[0].name)

    assert libbackup.check_library_archive(result.path, full=True)["assets"] == 2
    staged = libbackup.stage_library(result.paths[-1], settings, uid)
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
    zips = [n for n in names if n.endswith(".zip")]
    bigs = [n for n in names if n.endswith(".big")]
    assert len(zips) >= 2 and bigs
    # a big file has nothing to belong to until a zip of its backup is there
    early = client.put("/api/v1/backups/upload", params={"name": bigs[0]}, content=originals[bigs[0]])
    assert early.status_code == 400 and early.json()["code"] == "backup_invalid"
    for number, name in enumerate(list(reversed(zips)) + bigs, start=1):  # the zips in any order
        up = client.put("/api/v1/backups/upload", params={"name": name}, content=originals[name])
        assert up.status_code == 200, up.text
        body = up.json()
        assert name in [p["name"] for p in body["part_files"]]
        assert len(body["missing"]) == len(names) - number
    again = client.put("/api/v1/backups/upload", params={"name": zips[1]}, content=originals[zips[1]])
    assert again.status_code == 200  # the same part again
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
    assert not list(settings.backup_dir.glob("*.zip")) and not list(settings.backup_dir.glob("*.big"))


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

# --- a file bigger than a part is left beside the zips ---------------------------------------------


def _with_big_file(settings, client, size: int = 300_000) -> bytes:  # type: ignore[no-untyped-def]
    from authutil import scan_library
    from test_takeout import _jpeg_bytes

    data = _jpeg_bytes((200, 200, 9)) + bytes(range(256)) * (size // 256)
    (library_dir(settings, client) / "big.jpg").write_bytes(data)
    scan_library(client)
    return data


def test_a_file_bigger_than_a_part_is_a_plain_file_beside_the_zips(tmp_path: Path) -> None:
    src = _settings(tmp_path / "a")
    client = _seed(src)
    uid = me_id(client)
    data = _with_big_file(src, client)
    limit = 100_000
    result = backup_site(src, tmp_path / "out", split_bytes=limit)

    zips = _zips(result.paths)
    assert all(p.stat().st_size <= limit for p in zips), [p.stat().st_size for p in zips]
    big = next(p for p in _bigs(result.paths) if p.read_bytes() == data)  # the photo, byte for byte
    listed = {e["name"]: e for e in _manifest(zips[-1])["loose"]}
    assert listed[f"libraries/{uid}/big.jpg"]["part"] == int(big.name[-7:-4])
    assert result.manifest["file_count"] == 3  # the big file counts once, in the first zip
    assert sum(_manifest(z)["file_count"] for z in zips) == 3  # each zip counts what it holds, the first also the big file
    assert _manifest(zips[0])["file_count"] >= 1
    assert result.manifest["total_bytes"] == sum(
        p.stat().st_size for p in (src.data_dir / "libraries").rglob("*") if p.is_file()
    )

    assert check_archive(result.path, full=True)["file_count"] == 3
    dst = _settings(tmp_path / "b")
    restore_site(result.path, dst)
    restored = dst.data_dir / "libraries" / uid / "big.jpg"
    assert restored.read_bytes() == data
    assert int(restored.stat().st_mtime) == int((src.data_dir / "libraries" / uid / "big.jpg").stat().st_mtime)
    client.close()


def test_a_library_backup_leaves_a_big_file_beside_the_zips_too(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _seed(settings)
    uid = me_id(client)
    data = _with_big_file(settings, client)
    result = libbackup.backup_library(settings, uid, tmp_path / "out", split_bytes=100_000)
    assert all(p.stat().st_size <= 100_000 for p in _zips(result.paths))
    assert any(p.read_bytes() == data for p in _bigs(result.paths))
    assert result.manifest["file_count"] == 3
    assert libbackup.check_library_archive(result.path, full=True)["file_count"] == 3

    staged = libbackup.stage_library(result.path, settings, uid)
    try:
        assert (staged.data_stage / "libraries" / uid / "big.jpg").read_bytes() == data
    finally:
        import shutil

        shutil.rmtree(staged.db_stage, ignore_errors=True)
        shutil.rmtree(staged.data_stage, ignore_errors=True)
    client.close()


def test_a_lost_big_file_fails_the_check_and_nothing_is_staged(tmp_path: Path) -> None:
    src = _settings(tmp_path / "a")
    client = _seed(src)
    data = _with_big_file(src, client)
    result = backup_site(src, tmp_path / "out", split_bytes=100_000)
    next(p for p in _bigs(result.paths) if p.read_bytes() == data).unlink()
    with pytest.raises(PartsMissing):
        check_archive(result.path)
    dst = _settings(tmp_path / "b")
    with pytest.raises(PartsMissing):
        stage_site(result.path, dst)
    assert not list(dst.data_dir.glob(".restore-staging-*"))
    client.close()


def test_a_big_file_that_vanishes_before_it_is_copied_is_skipped(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    src = _settings(tmp_path / "a")
    client = _seed(src)
    uid = me_id(client)
    _with_big_file(src, client)
    big = src.data_dir / "libraries" / uid / "big.jpg"
    real = backup_site.__globals__["write_archive"]

    def deleting(*args, **kwargs):  # type: ignore[no-untyped-def]
        big.unlink()
        return real(*args, **kwargs)

    monkeypatch.setitem(backup_site.__globals__, "write_archive", deleting)
    result = backup_site(src, tmp_path / "out", split_bytes=100_000)
    assert result.manifest["file_count"] == 2
    assert any("disappeared" in w for w in result.manifest["warnings"])
    assert check_archive(result.path, full=True)["file_count"] == 2
    client.close()


def test_a_big_file_that_changes_while_it_is_copied_fails_the_backup(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from mosaicwave import archive

    src = _settings(tmp_path / "a")
    client = _seed(src)
    _with_big_file(src, client)
    real = archive._copy_big

    def growing(path, dest, size):  # type: ignore[no-untyped-def]
        return real(path, dest, size + 1)  # what the plan expected is not what was read

    monkeypatch.setattr(archive, "_copy_big", growing)
    with pytest.raises(ArchiveError, match="changed while"):
        backup_site(src, tmp_path / "out", split_bytes=100_000)
    assert list((tmp_path / "out").iterdir()) == []
    client.close()
