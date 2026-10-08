"""Inspect a Google Takeout *download* folder (zips + oversized files + extract)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from mosaicwave.library.media import (
    TAKEOUT_ZIP,
    is_date_folder,
    is_media,
    is_photos_root_name,
    parse_oversize_name,
    sidecar_media_name,
)
from mosaicwave.storage.takeoutzip import TakeoutZipStore, has_takeout_zips, zip_dump_has_media

SIDECAR_SAMPLES = 40


@dataclass(frozen=True)
class DumpWarning:
    code: str
    severity: str
    message: str
    path: str | None = None


@dataclass
class ZipSeries:
    stamp: str
    series: str
    parts: list[int] = field(default_factory=list)

    @property
    def missing_parts(self) -> list[int]:
        if not self.parts:
            return []
        have = set(self.parts)
        return [n for n in range(1, max(self.parts) + 1) if n not in have]


@dataclass
class LooseMedia:
    name: str
    size: int
    part: int | None
    original_name: str | None


@dataclass
class TakeoutInspect:
    path: str
    kind: str
    extract_root: str | None
    photos_root: str | None
    import_root: str | None
    zip_count: int
    series: list[ZipSeries]
    loose_media: list[LooseMedia]
    sidecar_without_media_count: int
    sidecar_without_media: list[str]
    album_metadata_count: int
    ready: bool
    warnings: list[DumpWarning]


def find_photos_root(start: Path) -> Path | None:
    """Extracted Photos tree under the *selected* folder. The selection's own name is ignored."""
    if not start.is_dir():
        return None
    takeout = start / "Takeout"
    bases = [start]
    if takeout.is_dir():
        bases.append(takeout)
    if start.name.lower() == "takeout":
        bases.append(start)
    seen: set[Path] = set()
    for base in bases:
        resolved = base.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        for child in sorted(base.iterdir(), key=lambda p: p.name.lower()):
            if child.is_dir() and is_photos_root_name(child.name) and _looks_like_photos_tree(child):
                return child
    if _looks_like_photos_tree(start):
        return start
    return None


def _looks_like_photos_tree(path: Path) -> bool:
    """Immediate date folders or media — not 'this directory is named Google Photos'."""
    if has_takeout_zips(path):
        return False
    try:
        kids = list(path.iterdir())
    except OSError:
        return False
    for item in kids:
        if item.is_file() and is_media(item.name):
            return True
        if item.is_dir() and is_date_folder(item.name):
            return True
    return False


def _child_zip_dumps(root: Path) -> list[str]:
    names: list[str] = []
    try:
        for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if child.is_dir() and has_takeout_zips(child):
                names.append(child.name)
    except OSError:
        return []
    return names


def _zip_series(root: Path) -> list[ZipSeries]:
    grouped: dict[tuple[str, str], list[int]] = {}
    for item in root.iterdir():
        if not item.is_file():
            continue
        match = TAKEOUT_ZIP.match(item.name)
        if not match:
            continue
        key = (match.group("stamp"), match.group("series") or "")
        grouped.setdefault(key, []).append(int(match.group("part")))
    out: list[ZipSeries] = []
    for (stamp, ser), parts in sorted(grouped.items()):
        out.append(ZipSeries(stamp=stamp, series=ser, parts=sorted(set(parts))))
    return out


def _loose_media(root: Path) -> list[LooseMedia]:
    found: list[LooseMedia] = []
    for item in root.iterdir():
        if not item.is_file() or not is_media(item.name):
            continue
        original, part = parse_oversize_name(item.name)
        found.append(
            LooseMedia(
                name=item.name,
                size=item.stat().st_size,
                part=part,
                original_name=original,
            )
        )
    return found


def _scan_extract(photos: Path) -> tuple[int, list[str], int, set[str]]:
    missing: list[str] = []
    missing_count = 0
    album_meta = 0
    all_media_names: set[str] = set()
    for dirpath, _dirnames, filenames in os.walk(photos, followlinks=False):
        names = set(filenames)
        if "metadata.json" in names:
            album_meta += 1
        for name in filenames:
            if is_media(name):
                all_media_names.add(name)
            media_name = sidecar_media_name(name)
            if not media_name or not is_media(media_name):
                continue
            if media_name not in names:
                missing_count += 1
                if len(missing) < SIDECAR_SAMPLES:
                    rel = (Path(dirpath) / media_name).relative_to(photos).as_posix()
                    missing.append(rel)
    return missing_count, missing, album_meta, all_media_names


def inspect_takeout_path(path: Path) -> TakeoutInspect:
    root = path.expanduser().resolve()
    warnings: list[DumpWarning] = []
    series = _zip_series(root) if root.is_dir() else []
    zip_count = sum(len(s.parts) for s in series)
    loose = _loose_media(root) if root.is_dir() else []
    if root.name.lower() == "takeout":
        takeout_dir = root
    else:
        takeout_dir = root / "Takeout"
    extract = takeout_dir if takeout_dir.is_dir() else None
    photos = find_photos_root(root)

    if zip_count == 0 and root.is_dir() and not has_takeout_zips(root):
        other_zips = sorted(
            p.name for p in root.iterdir() if p.is_file() and p.suffix.lower() == ".zip"
        )
        nested = _child_zip_dumps(root)
        hint = ""
        if nested:
            hint = (
                f" This folder has no takeout-*.zip files."
                f" Subfolder(s) that do: {', '.join(nested)}. Select that folder."
            )
        elif other_zips:
            hint = (
                f" Found zip file(s) with other names: {', '.join(other_zips[:8])}."
                " mosaicWave looks for takeout-<timestamp>-<part>.zip."
            )
        warnings.append(
            DumpWarning(
                code="no_takeout_zips",
                severity="error",
                message=(
                    "No takeout-*.zip archives in the selected folder."
                    + hint
                    + " An extracted tree needs a Google Photos folder inside this path (or date folders / photos here)."
                ),
                path=str(root),
            )
        )

    explained_parts = {item.part for item in loose if item.part is not None}
    for ser in series:
        label = ser.stamp + (f"-{ser.series}" if ser.series else "")
        for part in ser.missing_parts:
            if part in explained_parts:
                continue
            warnings.append(
                DumpWarning(
                    code="missing_zip_part",
                    severity="error",
                    message=(
                        f"Archive part {part:03d} of {label} is not on disk and no oversized "
                        f"file named *-{part:03d}.* was found. Re-download that zip."
                    ),
                )
            )

    sidecar_count = 0
    sidecar_samples: list[str] = []
    album_meta = 0
    import_root: str | None = None
    photos_label: str | None = str(photos) if photos else None

    kind = "unknown"
    if zip_count or loose:
        kind = "dump"
    elif photos is not None or extract is not None:
        kind = "extracted"

    if has_takeout_zips(root):
        if zip_count <= 8:
            store = TakeoutZipStore(root)
            for err in store.zip_errors:
                warnings.append(
                    DumpWarning(code="zip_unreadable", severity="error", message=err)
                )
            gaps = store.sidecar_gaps()
            sidecar_count = len(gaps)
            sidecar_samples = gaps[:SIDECAR_SAMPLES]
            album_meta = store.album_metadata_count()
            photos_label = store.photos_root_name
            mapped = {Path(rel).name.lower() for rel in store.media_rels()}
            found_media = bool(store.media_rels())
            for item in loose:
                orig = (item.original_name or item.name).lower()
                if orig not in mapped:
                    warnings.append(
                        DumpWarning(
                            code="loose_media_unexplained",
                            severity="warning",
                            message=(
                                f"{item.name} is next to the zips but no matching sidecar was found "
                                "in the archives, so it will not be imported."
                            ),
                            path=item.name,
                        )
                    )
        else:
            found_media, zip_errs = zip_dump_has_media(root)
            for err in zip_errs:
                warnings.append(DumpWarning(code="zip_unreadable", severity="error", message=err))
            warnings.append(
                DumpWarning(
                    code="check_partial",
                    severity="info",
                    message=(
                        "This dump has many zip archives. Check only looks until it finds photos; "
                        "import will read every archive."
                    ),
                )
            )
        if found_media:
            import_root = str(root)
            warnings.append(
                DumpWarning(
                    code="reads_zips",
                    severity="info",
                    message=(
                        f"Import reads {zip_count} zip archive(s) in place. You do not need to unzip. "
                        "Oversized videos next to the zips are attached using their sidecars."
                    ),
                )
            )
        else:
            warnings.append(
                DumpWarning(
                    code="zip_has_no_photos",
                    severity="error",
                    message="The zip files were opened but no photos/videos were found inside.",
                )
            )
        if sidecar_count:
            warnings.append(
                DumpWarning(
                    code="sidecar_without_media",
                    severity="warning",
                    message=(
                        f"{sidecar_count} photo/video sidecar(s) have no matching media file "
                        "in the zips or among oversized files next to them."
                    ),
                )
            )
        return TakeoutInspect(
            path=str(root),
            kind=kind,
            extract_root=str(extract) if extract else None,
            photos_root=photos_label,
            import_root=import_root,
            zip_count=zip_count,
            series=series,
            loose_media=loose,
            sidecar_without_media_count=sidecar_count,
            sidecar_without_media=sidecar_samples,
            album_metadata_count=album_meta,
            ready=import_root is not None,
            warnings=warnings,
        )

    import_root_path = photos
    if import_root_path is None and extract is not None:
        import_root_path = extract
    sidecar_count = 0
    sidecar_samples = []
    album_meta = 0
    if photos is not None:
        sidecar_count, sidecar_samples, album_meta, _media_names = _scan_extract(photos)
        if sidecar_count:
            warnings.append(
                DumpWarning(
                    code="sidecar_without_media",
                    severity="warning",
                    message=(
                        f"{sidecar_count} photo/video sidecar(s) have no matching media file."
                    ),
                    path=str(photos),
                )
            )

    ready = import_root_path is not None and import_root_path.is_dir()
    return TakeoutInspect(
        path=str(root),
        kind=kind,
        extract_root=str(extract) if extract else None,
        photos_root=str(photos) if photos else None,
        import_root=str(import_root_path) if import_root_path else None,
        zip_count=zip_count,
        series=series,
        loose_media=loose,
        sidecar_without_media_count=sidecar_count,
        sidecar_without_media=sidecar_samples,
        album_metadata_count=album_meta,
        ready=ready,
        warnings=warnings,
    )
