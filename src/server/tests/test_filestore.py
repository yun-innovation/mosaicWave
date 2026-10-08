from __future__ import annotations

from pathlib import Path

import pytest

from mosaicwave.storage.errors import InvalidPath, NotFound
from mosaicwave.storage.filestore import LocalFileStore, MemoryFileStore
from mosaicwave.storage.paths import normalize_rel, safe_library_filename


def test_normalize_rejects_parent_and_reserved() -> None:
    with pytest.raises(InvalidPath):
        normalize_rel("../secret")
    with pytest.raises(InvalidPath):
        normalize_rel("foo/../bar")
    with pytest.raises(InvalidPath):
        normalize_rel("CON")
    with pytest.raises(InvalidPath):
        normalize_rel("nul.txt")
    assert normalize_rel("album/café.jpg") == ("album", "café.jpg")
    assert normalize_rel("/a/b") == ("a", "b")


def test_safe_library_filename_strips_smb_illegal() -> None:
    assert safe_library_filename("Photo from 2:15 PM.jpg") == "Photo from 2_15 PM.jpg"
    assert safe_library_filename(r"a\\b<c>.jpg") == "b_c_.jpg"
    assert safe_library_filename("CON.jpg") == "CON_file.jpg"
    assert safe_library_filename("foo.") == "foo"


def _roundtrip(store: LocalFileStore | MemoryFileStore) -> None:
    with store.open_write("album/hello.jpg") as handle:
        handle.write(b"jpeg")
    assert store.exists("album/hello.jpg")
    assert "album" in store.list("")
    assert store.list("album") == ["hello.jpg"]
    assert list(store.walk()) == ["album/hello.jpg"]
    with store.open_read("album/hello.jpg") as handle:
        assert handle.read() == b"jpeg"
    st = store.stat("album/hello.jpg")
    assert st.size == 4
    assert not st.is_dir
    store.delete("album/hello.jpg")
    assert not store.exists("album/hello.jpg")


def test_local_filestore_roundtrip(tmp_path: Path) -> None:
    _roundtrip(LocalFileStore(tmp_path))


def test_memory_filestore_roundtrip() -> None:
    _roundtrip(MemoryFileStore())


def test_local_filestore_jail(tmp_path: Path) -> None:
    store = LocalFileStore(tmp_path / "root")
    (tmp_path / "outside.txt").write_bytes(b"nope")
    with pytest.raises(InvalidPath):
        store.stat("../outside.txt")
    with pytest.raises(InvalidPath):
        list(store.list(".."))


def test_local_filestore_unicode(tmp_path: Path) -> None:
    store = LocalFileStore(tmp_path)
    with store.open_write("日本/写真.jpg") as handle:
        handle.write(b"x")
    assert "写真.jpg" in store.list("日本")
    assert "日本/写真.jpg" in list(store.walk())


def test_local_filestore_not_found(tmp_path: Path) -> None:
    store = LocalFileStore(tmp_path)
    with pytest.raises(NotFound):
        store.stat("missing.jpg")
    with pytest.raises(NotFound):
        store.delete("missing.jpg")


def test_walk_skips_reserved_without_crash(tmp_path: Path) -> None:
    store = LocalFileStore(tmp_path)
    with store.open_write("ok.jpg") as handle:
        handle.write(b"1")
    assert list(store.walk()) == ["ok.jpg"]
    with pytest.raises(InvalidPath):
        store.exists("CON")
