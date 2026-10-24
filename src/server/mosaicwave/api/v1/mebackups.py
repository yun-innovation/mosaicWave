"""The signed-in user's own library backups: make, list, download, delete, upload, restore.

Archives live under ``backups/users/{user_id}/``, so a user only ever sees their own. Restoring swaps
that user's library while the server runs; nobody else is affected (doc/backup.md).
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from mosaicwave import restore
from mosaicwave.api.errors import ApiError
from mosaicwave.api.v1.archiveupload import UPLOAD_BODY, check_error, receive_archive
from mosaicwave.api.v1.library import ImportOut, _busy_error, _job_out
from mosaicwave.api.v1.maintenance import job_running
from mosaicwave.archive import LIBRARY_NAME, ArchiveError, read_manifest
from mosaicwave.auth.deps import require_user, require_user_brief
from mosaicwave.libbackup import check_library_archive, user_backup_dir
from mosaicwave.library.importjob import ImportBusy, ImportJobRunner
from mosaicwave.models import User

router = APIRouter()


class LibraryBackupOut(BaseModel):
    name: str
    size: int
    created_at: str
    app_version: str
    library_schema_version: int
    assets: int
    albums: int
    file_count: int
    total_bytes: int
    warnings: list[str] = Field(default_factory=list)


def _folder(request: Request, user: User) -> Path:
    return user_backup_dir(request.app.state.settings, user.id)


def _describe(path: Path) -> LibraryBackupOut | None:
    try:
        with zipfile.ZipFile(path) as zf:
            m = read_manifest(zf)
        return LibraryBackupOut(
            name=path.name,
            size=path.stat().st_size,
            created_at=str(m["created_at"]),
            app_version=str(m.get("app_version", "")),
            library_schema_version=int(m["library_schema_version"]),
            assets=int(m.get("assets", 0)),
            albums=int(m.get("albums", 0)),
            file_count=int(m.get("file_count", 0)),
            total_bytes=int(m.get("total_bytes", 0)),
            warnings=[str(w) for w in m.get("warnings", [])],
        )
    except (zipfile.BadZipFile, ArchiveError, KeyError, ValueError, OSError):
        return None


def _existing(request: Request, user: User, name: str) -> Path:
    path = _folder(request, user) / name
    if not LIBRARY_NAME.match(name) or not path.is_file():
        raise ApiError(404, "backup_not_found", "Backup not found", {"name": name})
    return path


@router.post("/me/backups", response_model=ImportOut)
def post_my_backup(request: Request, user: User = Depends(require_user)) -> ImportOut:
    runner: ImportJobRunner = request.app.state.import_jobs
    try:
        job = runner.start_library_backup(user_id=user.id)
    except ImportBusy as exc:
        raise _busy_error(exc) from exc
    return _job_out(job)


@router.get("/me/backups", response_model=list[LibraryBackupOut])
def get_my_backups(request: Request, user: User = Depends(require_user)) -> list[LibraryBackupOut]:
    folder = _folder(request, user)
    if not folder.is_dir():
        return []
    return [
        info
        for path in sorted(folder.iterdir(), key=lambda p: p.name, reverse=True)
        if LIBRARY_NAME.match(path.name) and (info := _describe(path)) is not None
    ]


# Declared before ``/me/backups/{name}`` so "upload" is not read as a backup name.
@router.put("/me/backups/upload", response_model=LibraryBackupOut, openapi_extra=UPLOAD_BODY)
async def upload_my_backup(request: Request, user: User = Depends(require_user_brief)) -> LibraryBackupOut:
    """Receive one of your library backups from the browser (raw body, streamed to disk)."""
    settings = request.app.state.settings
    if restore.pending_exists(settings):
        raise ApiError(409, "restore_pending", "A restore is waiting to be applied")
    session: Session = request.app.state.session_factory()
    try:
        if job_running(session):
            raise ApiError(409, "job_running", "Another job is already running")
    finally:
        session.close()
    final = await receive_archive(
        request, _folder(request, user), prefix="mosaicwave-library", check=check_library_archive
    )
    info = _describe(final)
    if info is None:
        raise ApiError(400, "backup_invalid", "The archive could not be read", {"message": "unreadable"})
    return info


@router.post("/me/backups/{name}/restore", response_model=ImportOut)
def restore_my_backup(name: str, request: Request, user: User = Depends(require_user)) -> ImportOut:
    """Replace your library with this backup. Your current library is kept aside by the server."""
    path = _existing(request, user, name)
    try:
        check_library_archive(path)
    except (ArchiveError, OSError) as exc:
        raise check_error(exc) from exc
    runner: ImportJobRunner = request.app.state.import_jobs
    try:
        job = runner.start_library_restore(user_id=user.id, archive=path)
    except ImportBusy as exc:
        raise _busy_error(exc) from exc
    return _job_out(job)


@router.get("/me/backups/{name}")
def download_my_backup(name: str, request: Request, user: User = Depends(require_user)) -> FileResponse:
    path = _existing(request, user, name)
    return FileResponse(path, media_type="application/zip", filename=name)


@router.delete("/me/backups/{name}")
def delete_my_backup(name: str, request: Request, user: User = Depends(require_user)) -> dict[str, bool]:
    _existing(request, user, name).unlink()
    return {"ok": True}
