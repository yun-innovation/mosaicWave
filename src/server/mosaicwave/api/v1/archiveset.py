"""Backups in a folder, grouped: one zip, or the parts of a split backup shown as one entry.

Shared by the site backup API and the per-user library backup API (doc/backup.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from mosaicwave.archive import PART_NAME, ArchiveError, merge_manifests, part_info, read_manifest_file


class PartOut(BaseModel):
    name: str
    size: int


class SetFields(BaseModel):
    """What every listed backup says about being in parts (mixed into the site and library models)."""

    parts: int = Field(default=1, description="How many files the backup has when complete (1: a single zip)")
    part_files: list[PartOut] = Field(default_factory=list, description="The parts that are here, in order")
    missing: list[int] = Field(default_factory=list, description="Part numbers not here yet")


@dataclass
class ListedSet:
    name: str  # the first file that is here; restore, delete and the listing use it
    base: str
    paths: list[Path]  # the files that are here, in part order
    manifest: dict  # the first file's, with counts and warnings added up over the files that are here
    parts: int
    missing: list[int]

    @property
    def size(self) -> int:
        return sum(p.stat().st_size for p in self.paths)

    def set_fields(self) -> dict:
        split = self.parts > 1
        return {
            "parts": self.parts,
            "part_files": [{"name": p.name, "size": p.stat().st_size} for p in self.paths] if split else [],
            "missing": self.missing,
        }


def _describe(base: str, found: dict[int, Path]) -> ListedSet | None:
    ordered = [found[n] for n in sorted(found)]
    try:
        manifests = [read_manifest_file(p) for p in ordered]
        info = part_info(manifests[0])
    except (ArchiveError, OSError):
        return None
    parts = info[1] if info else 1
    missing = [n for n in range(1, parts + 1) if n not in found] if info else []
    return ListedSet(ordered[0].name, base, ordered, merge_manifests(manifests), parts, missing)


def list_sets(folder: Path, name_re: re.Pattern[str]) -> list[ListedSet]:
    """Every backup in ``folder``, newest first. Unreadable files are left out."""
    if not folder.is_dir():
        return []
    singles: list[Path] = []
    split: dict[str, dict[int, Path]] = {}
    for path in folder.iterdir():
        if not name_re.match(path.name) or not path.is_file():
            continue
        found = PART_NAME.match(path.name)
        if found:
            split.setdefault(found.group(1), {})[int(found.group(2))] = path
        else:
            singles.append(path)
    out: list[ListedSet] = []
    for path in singles:
        listed = _describe(path.name[: -len(".zip")], {1: path})
        if listed is not None:
            out.append(listed)
    for base, found_parts in split.items():
        listed = _describe(base, found_parts)
        if listed is not None:
            out.append(listed)
    return sorted(out, key=lambda s: s.base, reverse=True)


def set_containing(folder: Path, name_re: re.Pattern[str], path: Path) -> ListedSet | None:
    """The backup ``path`` (a file in ``folder``) belongs to."""
    found = PART_NAME.match(path.name)
    base = found.group(1) if found else path.name[: -len(".zip")]
    for listed in list_sets(folder, name_re):
        if listed.base == base:
            return listed
    return None


def delete_set(path: Path) -> None:
    """Delete ``path``; for a part, every part of its backup that is in the same folder."""
    found = PART_NAME.match(path.name)
    if not found:
        path.unlink()
        return
    base = found.group(1)
    for other in path.parent.iterdir():
        match = PART_NAME.match(other.name)
        if match and match.group(1) == base:
            other.unlink(missing_ok=True)
