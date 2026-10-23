"""Site backups (admin only): start one, list, download, delete. Restore is offline."""

from __future__ import annotations

import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from mosaicwave.api.errors import ApiError
from mosaicwave.api.v1.library import ImportOut, _busy_error, _job_out
from mosaicwave.archive import SITE_NAME, ArchiveError, read_manifest
from mosaicwave.auth.deps import require_admin
from mosaicwave.library.importjob import ImportBusy, ImportJobRunner
from mosaicwave.models import User

router = APIRouter()


class BackupIn(BaseModel):
    include_thumbs: bool = False


class BackupOut(BaseModel):
    name: str
    size: int
    created_at: str
    app_version: str
    schema_version: int
    include_thumbs: bool
    users: int
    assets: int
    albums: int
    file_count: int
    total_bytes: int
    warnings: list[str] = Field(default_factory=list)


def _folder(request: Request) -> Path:
    return request.app.state.settings.backup_dir


def _describe(path: Path) -> BackupOut | None:
    try:
        with zipfile.ZipFile(path) as zf:
            m = read_manifest(zf)
        return BackupOut(
            name=path.name,
            size=path.stat().st_size,
            created_at=str(m["created_at"]),
            app_version=str(m.get("app_version", "")),
            schema_version=int(m["schema_version"]),
            include_thumbs=bool(m.get("include_thumbs", False)),
            users=int(m.get("users", 0)),
            assets=int(m.get("assets", 0)),
            albums=int(m.get("albums", 0)),
            file_count=int(m.get("file_count", 0)),
            total_bytes=int(m.get("total_bytes", 0)),
            warnings=[str(w) for w in m.get("warnings", [])],
        )
    except (zipfile.BadZipFile, ArchiveError, KeyError, ValueError, OSError):
        return None


def _existing(request: Request, name: str) -> Path:
    path = _folder(request) / name
    if not SITE_NAME.match(name) or not path.is_file():
        raise ApiError(404, "backup_not_found", "Backup not found", {"name": name})
    return path


@router.post("/backups", response_model=ImportOut)
def post_backup(
    request: Request, body: BackupIn | None = None, admin: User = Depends(require_admin)
) -> ImportOut:
    runner: ImportJobRunner = request.app.state.import_jobs
    try:
        job = runner.start_backup(user_id=admin.id, include_thumbs=bool(body and body.include_thumbs))
    except ImportBusy as exc:
        raise _busy_error(exc) from exc
    return _job_out(job)


@router.get("/backups", response_model=list[BackupOut])
def get_backups(request: Request, _admin: User = Depends(require_admin)) -> list[BackupOut]:
    folder = _folder(request)
    if not folder.is_dir():
        return []
    out = [
        info
        for path in sorted(folder.iterdir(), key=lambda p: p.name, reverse=True)
        if SITE_NAME.match(path.name) and (info := _describe(path)) is not None
    ]
    return out


@router.get("/backups/{name}")
def download_backup(name: str, request: Request, _admin: User = Depends(require_admin)) -> FileResponse:
    path = _existing(request, name)
    return FileResponse(path, media_type="application/zip", filename=name)


@router.delete("/backups/{name}")
def delete_backup(name: str, request: Request, _admin: User = Depends(require_admin)) -> dict[str, bool]:
    _existing(request, name).unlink()
    return {"ok": True}
