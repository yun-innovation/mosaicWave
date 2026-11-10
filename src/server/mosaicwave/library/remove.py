"""Deleting one photo from the library (DELETE /assets/{id}, doc/mobile.md § How an app learns a photo was removed).

The photo's row is kept as a tombstone (``deleted_at``) with its ``content_hash``, so a later
``POST /uploads/check`` can answer ``removed`` and an app does not send it again. The original file and its
thumbnails are deleted; albums lose it.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from mosaicwave.library.albums import remove_asset as remove_from_album
from mosaicwave.library.media import sha256_file
from mosaicwave.library.sync import assign_sync_rev
from mosaicwave.library.thumbs import preview_key, thumb_key
from mosaicwave.models import Album, AlbumAsset, Asset, utcnow
from mosaicwave.storage.blobstore import BlobStore
from mosaicwave.storage.errors import NotFound
from mosaicwave.storage.filestore import FileStore

_log = logging.getLogger(__name__)


def remove_photo(session: Session, asset: Asset, store: FileStore, blobs: BlobStore) -> None:
    """Delete ``asset``'s file and thumbnails, drop it from its albums and tombstone the row.

    The file goes first: if it cannot be deleted (``OSError``) nothing else has changed. Without the
    file the next scan would find it and bring the photo back.
    """
    rel = asset.relative_path
    present = store.exists(rel)
    if asset.content_hash is None and present:
        try:
            asset.content_hash = sha256_file(store, rel)  # the hash is what remembers it (never null on a tombstone if avoidable)
        except OSError as exc:
            _log.warning("could not hash %s before deleting it: %s", rel, exc)
    if present:
        try:
            store.delete(rel)
        except NotFound:
            pass  # gone already (deleted outside the app a moment ago): the goal is reached

    for key in (thumb_key(asset.id, asset.thumb_rev), preview_key(asset.id, asset.thumb_rev)):
        try:
            if blobs.exists(key):
                blobs.delete(key)
        except (OSError, NotFound):
            pass  # a thumbnail left behind is harmless

    albums = session.scalars(
        select(Album)
        .join(AlbumAsset, AlbumAsset.album_id == Album.id)
        .where(AlbumAsset.asset_id == asset.id, AlbumAsset.deleted_at.is_(None), Album.deleted_at.is_(None))
    ).all()
    for album in albums:
        remove_from_album(session, album, asset.id)

    asset.deleted_at = utcnow()
    assign_sync_rev(session, asset)
