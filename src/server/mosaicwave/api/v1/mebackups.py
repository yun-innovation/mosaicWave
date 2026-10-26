"""The signed-in user's own library backups: make, list, download, delete, upload, restore.

Archives live under ``backups/users/{user_id}/``, so a user only ever sees their own. Restoring swaps
that user's library while the server runs; nobody else is affected (doc/backup.md).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from mosaicwave import restore
from mosaicwave.api.errors import ApiError, wrapped
from mosaicwave.api.v1.archiveset import ListedSet, SetFields, delete_set, list_sets, set_containing
from mosaicwave.api.v1.archiveupload import UPLOAD_BODY, check_error, receive_archive
from mosaicwave.api.v1.library import ImportOut, _busy_error, _job_out
from mosaicwave.api.v1.maintenance import job_running
from mosaicwave.archive import LIBRARIES, LIBRARY_NAME, ArchiveError
from mosaicwave.auth.deps import require_user, require_user_brief
from mosaicwave.export import export_user_dir
from mosaicwave.libbackup import check_library_archive, user_backup_dir
from mosaicwave.library.importjob import ImportBusy, ImportJobRunner
from mosaicwave.models import User

router = APIRouter()


class LibraryBackupOut(SetFields):
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


def _describe(listed: ListedSet) -> LibraryBackupOut | None:
    m = listed.manifest
    try:
        return LibraryBackupOut(
            name=listed.name,
            size=listed.size,
            **listed.set_fields(),
            created_at=str(m["created_at"]),
            app_version=str(m.get("app_version", "")),
            library_schema_version=int(m["library_schema_version"]),
            assets=int(m.get("assets", 0)),
            albums=int(m.get("albums", 0)),
            file_count=int(m.get("file_count", 0)),
            total_bytes=int(m.get("total_bytes", 0)),
            warnings=[str(w) for w in m.get("warnings", [])],
        )
    except (ArchiveError, KeyError, ValueError, OSError):
        return None


def _existing(request: Request, user: User, name: str) -> Path:
    path = _folder(request, user) / name
    if not LIBRARY_NAME.match(name) or not path.is_file():
        raise ApiError(404, "backup_not_found", "Backup not found", {"name": name})
    return path


class LibraryBackupIn(BaseModel):
    split_gb: float | None = Field(
        default=None,
        ge=0.0001,
        le=1024,
        description="Part size in GB; a bigger backup is written as several zips. Empty: one zip",
    )


@router.post("/me/backups", response_model=ImportOut)
def post_my_backup(
    request: Request, body: LibraryBackupIn | None = None, user: User = Depends(require_user)
) -> ImportOut:
    runner: ImportJobRunner = request.app.state.import_jobs
    try:
        job = runner.start_library_backup(
            user_id=user.id, split_bytes=int(body.split_gb * 2**30) if body and body.split_gb else None
        )
    except ImportBusy as exc:
        raise _busy_error(exc) from exc
    return _job_out(job)


@router.get("/me/backups", response_model=list[LibraryBackupOut])
def get_my_backups(request: Request, user: User = Depends(require_user)) -> list[LibraryBackupOut]:
    return [
        info
        for listed in list_sets(_folder(request, user), LIBRARY_NAME)
        if (info := _describe(listed)) is not None
    ]


# Declared before ``/me/backups/{name}`` so "upload" is not read as a backup name.
@router.put("/me/backups/upload", response_model=LibraryBackupOut, openapi_extra=UPLOAD_BODY)
async def upload_my_backup(
    request: Request, name: str = Query(default="", max_length=255), user: User = Depends(require_user_brief)
) -> LibraryBackupOut:
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
    folder = _folder(request, user)
    final = await receive_archive(
        request,
        folder,
        prefix="mosaicwave-library",
        check=lambda path: check_library_archive(path, part_only=True),
        file_name=name,
    )
    listed = set_containing(folder, LIBRARY_NAME, final)
    info = _describe(listed) if listed is not None else None
    if info is None:
        raise ApiError(400, "backup_invalid", "The archive could not be read", {"message": "unreadable"})
    return info


class RestoreFromPathIn(BaseModel):
    path: str = Field(default="", max_length=4096, description="A library backup .zip on the server")


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _refuse_foreign(request: Request, user: User, path: Path) -> None:
    """Never restore from another user's library folder or archive folder (their photos)."""
    settings = request.app.state.settings
    for shared, own in (
        (settings.data_dir / LIBRARIES, settings.data_dir / LIBRARIES / user.id),
        (user_backup_dir(settings, user.id).parent, user_backup_dir(settings, user.id)),
        (export_user_dir(settings, user.id).parent, export_user_dir(settings, user.id)),
    ):
        if _is_under(path, shared) and not _is_under(path, own):
            raise ApiError(403, "folder_not_allowed", "not allowed")


@router.post("/me/backups/restore", response_model=ImportOut)
def restore_my_backup_from_path(
    body: RestoreFromPathIn, request: Request, user: User = Depends(require_user)
) -> ImportOut:
    """Replace your library with a zip anywhere on the server (a share, a USB drive). Admins only.

    Ordinary users restore from the device (upload) or from their own archive list: letting them name
    any server path would let them read other people's backups. Nothing is copied.
    """
    if user.role != "admin":
        raise ApiError(403, "admin_only", "admin only")
    text = body.path.strip()
    if not text:
        raise ApiError(400, "backup_path_required", "Choose the backup zip on the server")
    try:
        path = Path(text).expanduser().resolve()
        is_file = path.is_file()
    except OSError as exc:
        raise wrapped(400, "backup_invalid", exc) from exc
    if not is_file:
        raise ApiError(400, "backup_not_a_file", "That path is not a file", {"path": text})
    _refuse_foreign(request, user, path)
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


@router.post("/me/backups/{name}/restore", response_model=ImportOut)
def restore_my_backup(name: str, request: Request, user: User = Depends(require_user)) -> ImportOut:
    """Replace your library with this backup. Your current library is deleted once the restore succeeds."""
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
    delete_set(_existing(request, user, name))
    return {"ok": True}
