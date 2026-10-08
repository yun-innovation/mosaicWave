from __future__ import annotations

import glob
import os
import shutil
import subprocess
import tempfile
import threading
from io import BytesIO
from pathlib import Path, PurePosixPath

from PIL import Image, ImageDraw, ImageOps
from sqlalchemy.orm import Session

from mosaicwave.library.media import IMAGE_EXT, VIDEO_EXT, taken_at_from_exif
from mosaicwave.library.takeout import _touch, datetime_from_mtime, filestore_for_source
from mosaicwave.models import Asset, Source
from mosaicwave.storage.blobstore import BlobStore
from mosaicwave.storage.filestore import FileStore

THUMB_MAX = 400
PREVIEW_MAX = 1920
THUMB_QUALITY = 80
PREVIEW_QUALITY = 85
FFMPEG_TIMEOUT = 25

_VIDEO_PLACEHOLDER: bytes | None = None
_BROKEN_PLACEHOLDER: bytes | None = None
_PLACEHOLDER_HASHES: set[bytes] | None = None
_FFMPEG_UNSET = object()
_FFMPEG_BIN: str | None | object = _FFMPEG_UNSET
_FFMPEG_SLOTS = threading.Semaphore(1)
_RENDER_SLOTS = threading.Semaphore(2)
_SKIP_PLACEHOLDERS: set[str] = set()

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass


def thumb_key(asset_id: str, rev: int) -> str:
    return f"thumb/{asset_id}/{rev}.jpg"


def preview_key(asset_id: str, rev: int) -> str:
    return f"preview/{asset_id}/{rev}.jpg"


def is_image_path(rel: str) -> bool:
    return PurePosixPath(rel).suffix.lower() in IMAGE_EXT


def is_video_path(rel: str) -> bool:
    return PurePosixPath(rel).suffix.lower() in VIDEO_EXT


def _jpeg_rgb(img: Image.Image, *, quality: int) -> bytes:
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def _solid_jpeg(color: tuple[int, int, int], size: tuple[int, int] = (400, 300)) -> bytes:
    return _jpeg_rgb(Image.new("RGB", size, color), quality=70)


def _video_placeholder_jpeg() -> bytes:
    global _VIDEO_PLACEHOLDER
    if _VIDEO_PLACEHOLDER is None:
        img = Image.new("RGB", (400, 300), (48, 52, 62))
        draw = ImageDraw.Draw(img)
        draw.polygon([(155, 90), (155, 210), (275, 150)], fill=(196, 200, 210))
        _VIDEO_PLACEHOLDER = _jpeg_rgb(img, quality=70)
    return _VIDEO_PLACEHOLDER


def _broken_placeholder_jpeg() -> bytes:
    global _BROKEN_PLACEHOLDER
    if _BROKEN_PLACEHOLDER is None:
        _BROKEN_PLACEHOLDER = _solid_jpeg((48, 52, 62))
    return _BROKEN_PLACEHOLDER


def _placeholder_hashes() -> set[bytes]:
    global _PLACEHOLDER_HASHES
    if _PLACEHOLDER_HASHES is None:
        import hashlib

        blobs = [
            _video_placeholder_jpeg(),
            _broken_placeholder_jpeg(),
            _solid_jpeg((36, 38, 44)),
        ]
        extra: list[bytes] = []
        for blob in blobs:
            img = Image.open(BytesIO(blob)).convert("RGB")
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=70)
            extra.append(buf.getvalue())
        _PLACEHOLDER_HASHES = {hashlib.sha256(b).digest() for b in blobs + extra}
    return _PLACEHOLDER_HASHES


def is_placeholder_jpeg(data: bytes) -> bool:
    import hashlib

    if hashlib.sha256(data).digest() in _placeholder_hashes():
        return True
    try:
        img = Image.open(BytesIO(data)).convert("RGB")
    except Exception:
        return False
    if img.size != (400, 300):
        return False
    bg = img.getpixel((20, 20))
    mid = img.getpixel((200, 150))

    def near(a: tuple[int, int, int], b: tuple[int, int, int], tol: int = 18) -> bool:
        return all(abs(x - y) <= tol for x, y in zip(a, b))

    if near(bg, (48, 52, 62)) or near(bg, (36, 38, 44)):
        if mid[0] > bg[0] + 40:
            return True
        if near(mid, bg, 12):
            return True
    return False


def ffmpeg_bin() -> str | None:
    global _FFMPEG_BIN
    if _FFMPEG_BIN is not _FFMPEG_UNSET:
        return _FFMPEG_BIN  # type: ignore[return-value]
    found: str | None = None
    for candidate in _ffmpeg_candidates():
        if _ffmpeg_usable(candidate):
            found = candidate
            break
    _FFMPEG_BIN = found
    return found


def _ffmpeg_candidates() -> list[str]:
    out: list[str] = []
    env = (os.environ.get("MOSAICWAVE_FFMPEG") or "").strip()
    if env:
        out.append(env)
    try:
        import imageio_ffmpeg

        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled:
            out.append(bundled)
    except Exception:
        pass
    which = shutil.which("ffmpeg")
    if which:
        out.append(which)
    out.extend(
        [
            "/opt/bin/ffmpeg",
            "/usr/local/bin/ffmpeg",
            "/usr/bin/ffmpeg",
        ]
    )
    for pattern in (
        "/share/*/.qpkg/ffmpeg/ffmpeg",
        "/share/*/.qpkg/FFmpeg/ffmpeg",
        "/share/*/.qpkg/ffmpeg/bin/ffmpeg",
        "/share/*/.qpkg/FFmpeg/bin/ffmpeg",
    ):
        out.extend(glob.glob(pattern))
    seen: set[str] = set()
    unique: list[str] = []
    for path in out:
        if path and path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def _ffmpeg_usable(path: str) -> bool:
    exe = Path(path)
    if not exe.is_file():
        return False
    kwargs: dict = {"capture_output": True, "timeout": 4, "check": False}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.run([str(exe), "-hide_banner", "-version"], **kwargs)
    except (OSError, subprocess.TimeoutExpired):
        return False
    text = ((proc.stdout or b"") + (proc.stderr or b"")).decode("utf-8", errors="replace").lower()
    return proc.returncode == 0 and "ffmpeg" in text


def _run_ffmpeg(src: str, dest: str, max_edge: int = THUMB_MAX, *, still: bool = False) -> bool:
    del max_edge  # frame is resized in Pillow after extract
    bin_path = ffmpeg_bin()
    if not bin_path:
        return False
    kwargs: dict = {
        "timeout": FFMPEG_TIMEOUT,
        "check": False,
        "capture_output": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        kwargs["preexec_fn"] = _nice_child
    seeks: list[list[str]] = [[]] if still else [["-ss", "0.5"], ["-ss", "0"]]
    with _FFMPEG_SLOTS:
        for use_update in (True, False):
            for seek in seeks:
                Path(dest).unlink(missing_ok=True)
                cmd = [
                    bin_path,
                    "-y",
                    "-hide_banner",
                    "-nostdin",
                    "-loglevel",
                    "error",
                    "-threads",
                    "1",
                    *seek,
                    "-i",
                    src,
                    "-an",
                    "-frames:v",
                    "1",
                ]
                if use_update:
                    cmd.extend(["-update", "1"])
                cmd.extend(["-q:v", "4", dest])
                try:
                    result = subprocess.run(cmd, **kwargs)
                except subprocess.TimeoutExpired:
                    continue
                except OSError:
                    return False
                if result.returncode == 0 and Path(dest).is_file() and Path(dest).stat().st_size > 0:
                    return True
    return False


def _nice_child() -> None:
    try:
        os.nice(10)
    except OSError:
        pass


def _spool_to_temp(store: FileStore, rel: str) -> str:
    suffix = PurePosixPath(rel).suffix or ".bin"
    fd, tmp = tempfile.mkstemp(prefix="mw-thumb-", suffix=suffix)
    with os.fdopen(fd, "wb") as out:
        with store.open_read(rel) as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
    return tmp


def _ffmpeg_source_path(store: FileStore, rel: str) -> tuple[str, bool]:
    with store.open_read(rel) as handle:
        name = getattr(handle, "name", None)
        if isinstance(name, (str, os.PathLike)) and Path(name).is_file():
            return str(name), False
    return _spool_to_temp(store, rel), True


def _ffmpeg_frame_jpeg(
    store: FileStore,
    rel: str,
    max_edge: int,
    *,
    still: bool = False,
) -> tuple[bytes, int, int] | None:
    src, is_temp = _ffmpeg_source_path(store, rel)
    fd, dest = tempfile.mkstemp(prefix="mw-frame-", suffix=".jpg")
    os.close(fd)
    try:
        if not _run_ffmpeg(src, dest, max_edge=max_edge, still=still):
            return None
        data = Path(dest).read_bytes()
        image = Image.open(BytesIO(data))
        image.load()
        width, height = image.size
        if max(image.size) > max_edge:
            image.thumbnail((max_edge, max_edge))
            data = _jpeg_rgb(image, quality=THUMB_QUALITY)
        return data, width, height
    except Exception:
        return None
    finally:
        Path(dest).unlink(missing_ok=True)
        if is_temp:
            Path(src).unlink(missing_ok=True)


def render_video_frame_jpeg(store: FileStore, rel: str) -> tuple[bytes, int, int] | None:
    return _ffmpeg_frame_jpeg(store, rel, THUMB_MAX, still=False)


def _open_pil_image(data: bytes, suffix: str) -> Image.Image:
    try:
        image = Image.open(BytesIO(data))
        image.load()
        return image
    except Exception:
        pass
    fd, tmp = tempfile.mkstemp(prefix="mw-img-", suffix=suffix or ".bin")
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        image = Image.open(tmp)
        image.load()
        return image
    finally:
        Path(tmp).unlink(missing_ok=True)


def _fit_jpeg(image: Image.Image, max_edge: int, quality: int) -> tuple[bytes, int, int]:
    image = ImageOps.exif_transpose(image)
    width, height = image.size
    image.thumbnail((max_edge, max_edge))
    return _jpeg_rgb(image, quality=quality), width, height


def render_image_jpeg(store: FileStore, rel: str, max_edge: int) -> tuple[bytes, int, int]:
    suffix = PurePosixPath(rel).suffix or ".bin"
    with store.open_read(rel) as handle:
        data = handle.read()
    quality = THUMB_QUALITY if max_edge <= THUMB_MAX else PREVIEW_QUALITY
    try:
        return _fit_jpeg(_open_pil_image(data, suffix), max_edge, quality)
    except Exception:
        frame = _ffmpeg_frame_jpeg(store, rel, max_edge, still=True)
        if frame is not None:
            return frame
        raise


def render_image_thumb_jpeg(store: FileStore, rel: str) -> tuple[bytes, int, int]:
    return render_image_jpeg(store, rel, THUMB_MAX)


def render_thumb_jpeg(store: FileStore, rel: str) -> tuple[bytes, int | None, int | None]:
    if is_video_path(rel):
        frame = render_video_frame_jpeg(store, rel)
        if frame is not None:
            return frame
        return _video_placeholder_jpeg(), None, None
    if not is_image_path(rel):
        return _broken_placeholder_jpeg(), None, None
    try:
        return render_image_jpeg(store, rel, THUMB_MAX)
    except Exception:
        return _broken_placeholder_jpeg(), None, None


def clear_placeholder_skips() -> None:
    _SKIP_PLACEHOLDERS.clear()


def _cached_good_thumb(blobs: BlobStore, asset: Asset) -> str | None:
    if not asset.thumb_rev:
        return None
    key = thumb_key(asset.id, asset.thumb_rev)
    if not blobs.exists(key):
        return None
    with blobs.get(key) as handle:
        existing = handle.read()
    if is_placeholder_jpeg(existing):
        return None
    return key


def ensure_thumb(
    session: Session,
    blobs: BlobStore,
    asset: Asset,
    source: Source,
) -> str:
    key = _cached_good_thumb(blobs, asset)
    if key:
        return key
    if asset.thumb_rev and asset.id in _SKIP_PLACEHOLDERS:
        skipped = thumb_key(asset.id, asset.thumb_rev)
        if blobs.exists(skipped):
            return skipped
    with _RENDER_SLOTS:
        key = _cached_good_thumb(blobs, asset)
        if key:
            return key
        if asset.thumb_rev and asset.id in _SKIP_PLACEHOLDERS:
            skipped = thumb_key(asset.id, asset.thumb_rev)
            if blobs.exists(skipped):
                return skipped
        store = filestore_for_source(source)
        try:
            jpeg, width, height = render_thumb_jpeg(store, asset.relative_path)
        except Exception:
            jpeg, width, height = _broken_placeholder_jpeg(), asset.width, asset.height
            if is_video_path(asset.relative_path):
                jpeg = _video_placeholder_jpeg()
        if width and asset.width != width:
            asset.width = width
        if height and asset.height != height:
            asset.height = height
        if asset.taken_at is None and is_image_path(asset.relative_path):
            taken = taken_at_from_exif(store, asset.relative_path)
            if taken is None:
                try:
                    taken = datetime_from_mtime(store.stat(asset.relative_path).mtime)
                except Exception:
                    taken = None
            if taken is not None:
                asset.taken_at = taken
        if is_placeholder_jpeg(jpeg):
            _SKIP_PLACEHOLDERS.add(asset.id)
        rev = int(asset.thumb_rev or 0) + 1
        key = thumb_key(asset.id, rev)
        blobs.put(key, BytesIO(jpeg))
        asset.thumb_rev = rev
        try:
            _touch(asset)
        except Exception as exc:
            from mosaicwave.db import sqlite_locked

            if sqlite_locked(exc):
                return key
            raise
        return key


def ensure_preview(
    session: Session,
    blobs: BlobStore,
    asset: Asset,
    source: Source,
) -> str:
    thumb = ensure_thumb(session, blobs, asset, source)
    rev = int(asset.thumb_rev or 1)
    key = preview_key(asset.id, rev)
    if blobs.exists(key):
        return key
    if is_video_path(asset.relative_path):
        with blobs.get(thumb) as handle:
            blobs.put(key, BytesIO(handle.read()))
        return key
    store = filestore_for_source(source)
    try:
        jpeg, width, height = render_image_jpeg(store, asset.relative_path, PREVIEW_MAX)
    except Exception:
        jpeg, width, height = _broken_placeholder_jpeg(), asset.width, asset.height
    if width and asset.width != width:
        asset.width = width
    if height and asset.height != height:
        asset.height = height
    blobs.put(key, BytesIO(jpeg))
    _touch(asset)
    return key
