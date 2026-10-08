from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest

from mosaicwave.storage.blobstore import LocalBlobStore, MemoryBlobStore
from mosaicwave.storage.errors import InvalidPath, NotFound
from mosaicwave.storage.paths import normalize_blob_key


def test_blob_key_rules() -> None:
    assert normalize_blob_key("thumb/abc/1.jpg") == "thumb/abc/1.jpg"
    with pytest.raises(InvalidPath):
        normalize_blob_key("../x")
    with pytest.raises(InvalidPath):
        normalize_blob_key("/thumb/x")
    with pytest.raises(InvalidPath):
        normalize_blob_key("thumb//x")


def _roundtrip(store: LocalBlobStore | MemoryBlobStore) -> None:
    key = "thumb/asset-id/1.jpg"
    store.put(key, BytesIO(b"thumb-bytes"))
    assert store.exists(key)
    assert store.stat(key).size == 11
    with store.get(key) as handle:
        assert handle.read() == b"thumb-bytes"
    store.put(key, BytesIO(b"replaced"))
    with store.get(key) as handle:
        assert handle.read() == b"replaced"
    store.delete(key)
    assert not store.exists(key)
    with pytest.raises(NotFound):
        store.delete(key)


def test_local_blobstore(tmp_path: Path) -> None:
    _roundtrip(LocalBlobStore(tmp_path / "blobs"))


def test_memory_blobstore() -> None:
    _roundtrip(MemoryBlobStore())
