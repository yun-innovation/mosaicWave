"""Receive a backup zip from the browser into a folder (shared by the site and library backup APIs)."""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Request
from starlette.requests import ClientDisconnect

from mosaicwave.api.errors import ApiError, wrapped
from mosaicwave.archive import ArchiveError, WrongKind
from mosaicwave.library.takeoutpush import MAX_PUSH_BYTES, write_upload_async
from mosaicwave.models import new_id

UPLOAD_BODY = {
    "requestBody": {
        "required": True,
        "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    }
}

_STAMP = re.compile(r"(\d{8}-\d{6})\.zip$")


def archive_name(prefix: str, manifest: dict) -> str:
    """The standard name for an uploaded archive, from the time inside it (so a renamed file works)."""
    try:
        stamp = datetime.fromisoformat(str(manifest["created_at"]))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        stamp = stamp.astimezone(timezone.utc)
    except (KeyError, ValueError):
        stamp = datetime.now(timezone.utc)
    return f"{prefix}-{stamp:%Y%m%d-%H%M%S}.zip"


def check_error(exc: BaseException) -> ApiError:
    """``400`` for an archive that cannot be used; the wrong kind of archive has its own code."""
    if isinstance(exc, WrongKind):
        return wrapped(400, "backup_wrong_kind", exc)
    return wrapped(400, "backup_invalid", exc)


async def receive_archive(
    request: Request,
    folder: Path,
    *,
    prefix: str,
    check: Callable[[Path], dict],
) -> Path:
    """Stream the request body to ``folder``, validate it with ``check`` and name it ``{prefix}-stamp.zip``.

    The caller has already refused when the server is busy. Returns the archive's final path.
    """
    folder.mkdir(parents=True, exist_ok=True)
    raw = request.headers.get("content-length")
    declared = int(raw) if raw and raw.isdigit() else None
    if declared is not None:
        if declared > MAX_PUSH_BYTES:
            raise ApiError(413, "upload_too_large", "That file is too large", {"max_gb": MAX_PUSH_BYTES >> 30})
        free = shutil.disk_usage(folder).free
        if free < declared + declared // 20:
            raise ApiError(
                507,
                "backup_no_space",
                "Not enough free space for that file",
                {"need_mb": declared >> 20, "free_mb": free >> 20},
            )

    received = folder / f".upload-{new_id()}.zip"
    try:
        try:
            await write_upload_async(received, request.stream())
        except ClientDisconnect as exc:
            raise ApiError(400, "upload_interrupted", "upload interrupted") from exc
        except ValueError as exc:
            raise ApiError(413, "upload_too_large", str(exc), {"max_gb": MAX_PUSH_BYTES >> 30}) from exc
        except OSError as exc:
            raise wrapped(400, "upload_failed", exc) from exc
        try:
            manifest = check(received)
        except (ArchiveError, OSError) as exc:
            raise check_error(exc) from exc
        final = folder / archive_name(prefix, manifest)
        size = received.stat().st_size
        while final.exists():
            if final.stat().st_size == size:  # the same file again
                break
            found = _STAMP.search(final.name)
            stamp = datetime.strptime(found.group(1), "%Y%m%d-%H%M%S") if found else datetime.now(timezone.utc)
            final = folder / f"{prefix}-{stamp + timedelta(seconds=1):%Y%m%d-%H%M%S}.zip"
        if not final.exists():
            os.replace(received, final)
        return final
    finally:
        received.unlink(missing_ok=True)
