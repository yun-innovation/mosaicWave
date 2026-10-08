from __future__ import annotations

from collections.abc import Iterator
from pathlib import PurePosixPath
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse

from mosaicwave.storage.errors import NotFound, PermissionDenied
from mosaicwave.storage.filestore import FileStore

CHUNK = 1024 * 1024


def parse_bytes_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Inclusive start/end, or None for the full file. Raises ValueError if unsatisfiable."""
    if not header:
        return None
    lowered = header.strip().lower()
    if not lowered.startswith("bytes="):
        return None
    spec = header.split("=", 1)[1].strip()
    if "," in spec:
        spec = spec.split(",", 1)[0].strip()
    if "-" not in spec:
        raise ValueError("invalid range")
    start_s, end_s = spec.split("-", 1)
    if size <= 0:
        raise ValueError("unsatisfiable")
    if start_s == "":
        suffix = int(end_s)
        if suffix <= 0:
            raise ValueError("invalid range")
        start = max(0, size - suffix)
        end = size - 1
    else:
        start = int(start_s)
        end = int(end_s) if end_s else size - 1
        if start < 0:
            raise ValueError("invalid range")
    if start >= size or end < start:
        raise ValueError("unsatisfiable")
    return start, min(end, size - 1)


def _disposition(filename: str, *, download: bool) -> str:
    kind = "attachment" if download else "inline"
    ascii_name = filename.encode("ascii", "replace").decode("ascii").replace('"', "")
    encoded = quote(filename)
    return f'{kind}; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded}'


def _iter_file(
    store: FileStore,
    rel: str,
    start: int,
    length: int | None,
) -> Iterator[bytes]:
    remaining = length
    with store.open_read(rel) as handle:
        if start:
            if handle.seekable():
                handle.seek(start)
            else:
                skipped = 0
                while skipped < start:
                    data = handle.read(min(CHUNK, start - skipped))
                    if not data:
                        break
                    skipped += len(data)
        while remaining is None or remaining > 0:
            n = CHUNK if remaining is None else min(CHUNK, remaining)
            chunk = handle.read(n)
            if not chunk:
                break
            if remaining is not None:
                remaining -= len(chunk)
            yield chunk


def original_file_response(
    store: FileStore,
    rel: str,
    *,
    mime: str,
    size: int,
    range_header: str | None,
    download: bool,
) -> Response:
    filename = PurePosixPath(rel).name
    try:
        size = store.stat(rel).size
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="file not found") from exc
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": _disposition(filename, download=download),
    }
    try:
        rng = parse_bytes_range(range_header, size)
    except ValueError:
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"},
        )

    if rng is None:
        headers["Content-Length"] = str(size)
        return StreamingResponse(
            _iter_file(store, rel, 0, None),
            media_type=mime,
            headers=headers,
        )

    start, end = rng
    length = end - start + 1
    headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    headers["Content-Length"] = str(length)
    return StreamingResponse(
        _iter_file(store, rel, start, length),
        status_code=206,
        media_type=mime,
        headers=headers,
    )
