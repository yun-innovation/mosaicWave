from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path, PurePosixPath

from mosaicwave.storage.filestore import FileStore

IMAGE_EXT = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".tif", ".tiff", ".bmp", ".dng"}
)
VIDEO_EXT = frozenset({".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".3gp"})
MEDIA_EXT = IMAGE_EXT | VIDEO_EXT

TAKEOUT_ZIP = re.compile(
    r"^takeout-(?P<stamp>\d{8}T\d{6}Z)(?:-(?P<series>\d+))?-(?P<part>\d+)\.zip$",
    re.IGNORECASE,
)
OVERSIZE_SUFFIX = re.compile(
    r"^(?P<stem>.+)-(?P<part>\d{3})(?P<ext>\.[A-Za-z0-9]+)$",
)

DATE_FOLDER = re.compile(r"^Photos from \d{4}$", re.IGNORECASE)
# Locale year buckets: 2021的相片, 2021相簿, 2021相册, 2021年の写真
DATE_FOLDER_LOCALE = re.compile(r"^\d{4}(的相片|相簿|年相册|相册|年の写真)$")
PHOTOS_ROOT_NAMES = frozenset(
    {
        "google photos",
        "photos",
        "google 相簿",
        "google 相片",
        "google 相册",
        "google フォト",
    }
)


def is_media(rel: str) -> bool:
    return PurePosixPath(rel).suffix.lower() in MEDIA_EXT


def sidecar_media_name(name: str) -> str | None:
    """If this is a Takeout per-file sidecar, return the media filename it belongs to."""
    lower = name.lower()
    if lower == "metadata.json":
        return None
    if lower.endswith(".supplemental-metadata.json"):
        return name[: -len(".supplemental-metadata.json")]
    if lower.endswith(".json"):
        return name[:-5]
    return None


def parse_oversize_name(name: str) -> tuple[str | None, int | None]:
    """Return (original filename, zip part) for dump-root oversized files."""
    match = OVERSIZE_SUFFIX.match(name)
    if not match:
        return None, None
    ext = match.group("ext")
    if ext.lower() not in MEDIA_EXT:
        return None, None
    return f"{match.group('stem')}{ext}", int(match.group("part"))


def parent_name(rel: str) -> str:
    return PurePosixPath(rel).parent.name


def filename(rel: str) -> str:
    return PurePosixPath(rel).name


def is_photos_root_name(name: str) -> bool:
    """Folder name Google uses *inside* a Takeout tree or zip (`Google Photos`, `Google 相片`, …).

    Do not apply this to the path the user selected. A download directory can be named
    anything; inspect uses that folder as-is.
    """
    lower = name.strip().lower()
    if lower in PHOTOS_ROOT_NAMES or lower == "photos":
        return True
    return lower.startswith("google ")


def is_date_folder(name: str) -> bool:
    text = name.strip()
    return bool(DATE_FOLDER.match(text) or DATE_FOLDER_LOCALE.match(text))


def is_album_folder(name: str) -> bool:
    if not name:
        return False
    if is_date_folder(name):
        return False
    if is_photos_root_name(name):
        return False
    return True


def guess_mime(rel: str) -> str:
    mime, _ = mimetypes.guess_type(rel)
    if mime:
        return mime
    ext = PurePosixPath(rel).suffix.lower()
    if ext in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if ext in {".png"}:
        return "image/png"
    if ext in {".gif"}:
        return "image/gif"
    if ext in {".webp"}:
        return "image/webp"
    if ext in {".bmp"}:
        return "image/bmp"
    if ext in {".heic", ".heif"}:
        return "image/heic"
    if ext in {".tif", ".tiff"}:
        return "image/tiff"
    if ext == ".dng":
        return "image/dng"
    if ext in VIDEO_EXT:
        return "video/mp4"
    return "application/octet-stream"


def sha256_file(store: FileStore, rel: str) -> str:
    digest = hashlib.sha256()
    with store.open_read(rel) as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _sidecar_candidates(rel: str) -> list[str]:
    """Takeout JSON next to the file, plus Live Photo still sidecar for videos."""
    path = PurePosixPath(rel)
    names = [f"{rel}.json", f"{rel}.supplemental-metadata.json"]
    if path.suffix.lower() not in VIDEO_EXT:
        return names
    parent = path.parent
    stem = path.stem
    for ext in (".HEIC", ".heic", ".HEIF", ".heif", ".JPG", ".jpg", ".JPEG", ".jpeg"):
        still = f"{stem}{ext}"
        still_rel = still if str(parent) == "." else f"{parent.as_posix()}/{still}"
        names.extend(
            (
                f"{still_rel}.json",
                f"{still_rel}.supplemental-metadata.json",
            )
        )
    return names


def read_sidecar(store: FileStore, rel: str) -> dict | None:
    for path in _sidecar_candidates(rel):
        if not store.exists(path):
            continue
        try:
            with store.open_read(path) as handle:
                raw = handle.read()
            data = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            return data
    return None


def taken_at_from_sidecar(sidecar: dict) -> datetime | None:
    block = sidecar.get("photoTakenTime") or sidecar.get("creationTime")
    if not isinstance(block, dict):
        return None
    ts = block.get("timestamp")
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _ffprobe_bin() -> str | None:
    found = shutil.which("ffprobe")
    if found:
        return found
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    sibling = Path(ffmpeg).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
    return str(sibling) if sibling.is_file() else None


def taken_at_from_video(store: FileStore, rel: str) -> datetime | None:
    if PurePosixPath(rel).suffix.lower() not in VIDEO_EXT:
        return None
    probe = _ffprobe_bin()
    if not probe:
        return None
    src: str | None = None
    is_temp = False
    with store.open_read(rel) as handle:
        name = getattr(handle, "name", None)
        if isinstance(name, (str, os.PathLike)) and Path(name).is_file():
            src = str(name)
        else:
            suffix = PurePosixPath(rel).suffix or ".bin"
            fd, src = tempfile.mkstemp(prefix="mw-probe-", suffix=suffix)
            is_temp = True
            with os.fdopen(fd, "wb") as out:
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
    if src is None:
        return None
    kwargs: dict = {"capture_output": True, "check": False, "timeout": 30}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    cmd = [
        probe,
        "-v",
        "error",
        "-show_entries",
        "format_tags=creation_time",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        src,
    ]
    try:
        result = subprocess.run(cmd, **kwargs)
        raw = (result.stdout or b"").decode("utf-8", "replace").strip()
        if result.returncode != 0 or not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        taken = datetime.fromisoformat(raw)
        if taken.tzinfo is None:
            taken = taken.replace(tzinfo=timezone.utc)
        return taken.astimezone(timezone.utc)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    finally:
        if is_temp and src:
            Path(src).unlink(missing_ok=True)


def taken_at_from_exif(store: FileStore, rel: str) -> datetime | None:
    if PurePosixPath(rel).suffix.lower() not in IMAGE_EXT:
        return None
    try:
        from PIL import Image, ExifTags
        try:
            from pillow_heif import register_heif_opener

            register_heif_opener()
        except ImportError:
            pass
    except ImportError:
        return None
    try:
        with store.open_read(rel) as handle:
            data = handle.read()
        image = Image.open(BytesIO(data))
        exif = image.getexif()
        if not exif:
            return None
        tags = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
        raw = tags.get("DateTimeOriginal") or tags.get("DateTime")
        if not isinstance(raw, str):
            return None
        return datetime.strptime(raw, "%Y:%m:%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def extra_from_sidecar(sidecar: dict) -> dict:
    extra: dict = {}
    geo = sidecar.get("geoData") or sidecar.get("geoDataExif")
    if isinstance(geo, dict):
        extra["geo"] = {
            "lat": geo.get("latitude"),
            "lng": geo.get("longitude"),
            "alt": geo.get("altitude"),
        }
    desc = sidecar.get("description")
    if isinstance(desc, str) and desc:
        extra["caption"] = desc
    title = sidecar.get("title")
    if isinstance(title, str) and title:
        extra["title"] = title
    return extra
