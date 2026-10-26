"""Site backups (admin only): start one, list, download, delete, and restore one (stage, then apply)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from mosaicwave import maintenance, restore
from mosaicwave.api.errors import ApiError, wrapped
from mosaicwave.api.v1.archiveset import ListedSet, SetFields, delete_set, list_sets, set_containing
from mosaicwave.api.v1.archiveupload import UPLOAD_BODY, check_error, receive_archive
from mosaicwave.api.v1.library import ImportOut, _busy_error, _job_out
from mosaicwave.api.v1.maintenance import job_running
from mosaicwave.archive import SITE_NAME, ArchiveError
from mosaicwave.auth.deps import require_admin, require_user_brief
from mosaicwave.config import schedule_api_restart
from mosaicwave.db import get_session
from mosaicwave.library.importjob import ImportBusy, ImportJobRunner
from mosaicwave.models import User

router = APIRouter()


class BackupIn(BaseModel):
    include_thumbs: bool = False
    split_gb: float | None = Field(
        default=None,
        ge=0.0001,
        le=1024,
        description="Part size in GB; a bigger backup is written as several zips. Empty: one zip",
    )


class BackupOut(SetFields):
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


def _describe(listed: ListedSet) -> BackupOut | None:
    m = listed.manifest
    try:
        return BackupOut(
            name=listed.name,
            size=listed.size,
            **listed.set_fields(),
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
    except (ArchiveError, KeyError, ValueError, OSError):
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
        job = runner.start_backup(
            user_id=admin.id,
            include_thumbs=bool(body and body.include_thumbs),
            split_bytes=int(body.split_gb * 2**30) if body and body.split_gb else None,
        )
    except ImportBusy as exc:
        raise _busy_error(exc) from exc
    return _job_out(job)


@router.get("/backups", response_model=list[BackupOut])
def get_backups(request: Request, _admin: User = Depends(require_admin)) -> list[BackupOut]:
    return [info for listed in list_sets(_folder(request), SITE_NAME) if (info := _describe(listed)) is not None]


class RestoreSummary(BaseModel):
    created_at: str | None = None
    app_version: str | None = None
    schema_version: int | None = None
    include_thumbs: bool = False
    users: int = 0
    assets: int = 0
    albums: int = 0
    file_count: int = 0


class RestorePendingOut(BaseModel):
    archive: str
    staged_at: str
    summary: RestoreSummary
    upgrade: bool = Field(description="The archive is from an older version; its databases upgrade at start")


class RestoreResultOut(BaseModel):
    status: str = Field(examples=["done", "error"])
    archive: str
    finished_at: str
    summary: RestoreSummary
    kept: list[str] = Field(default_factory=list, description="Where the previous data was kept (empty: an online restore deletes it)")
    upgrade: bool = False
    thumbs_missing: bool = False
    message: str | None = None


class RestoreStateOut(BaseModel):
    pending: RestorePendingOut | None = None
    result: RestoreResultOut | None = None


class RestartOut(BaseModel):
    restarting: bool


def _summary(raw: object) -> RestoreSummary:
    data = raw if isinstance(raw, dict) else {}
    return RestoreSummary(**{k: v for k, v in data.items() if k in RestoreSummary.model_fields and v is not None})


def _restore_state(settings) -> RestoreStateOut:  # type: ignore[no-untyped-def]
    pending = restore.read_pending(settings)
    result = restore.read_result(settings)
    out = RestoreStateOut()
    if pending is not None:
        out.pending = RestorePendingOut(
            archive=str(pending.get("archive", "")),
            staged_at=str(pending.get("staged_at", "")),
            summary=_summary(pending.get("summary")),
            upgrade=pending.get("site_upgrade_from") is not None
            or pending.get("library_upgrade_from") is not None,
        )
    if result is not None and result.get("status") in ("done", "error"):
        out.result = RestoreResultOut(
            status=str(result["status"]),
            archive=str(result.get("archive", "")),
            finished_at=str(result.get("finished_at", "")),
            summary=_summary(result.get("summary")),
            kept=[str(k) for k in result.get("kept") or []],
            upgrade=bool(result.get("upgrade")),
            thumbs_missing=bool(result.get("thumbs_missing")),
            message=result.get("message"),
        )
    return out


# Declared before ``/backups/{name}`` so "restore" is not read as a backup name.
@router.get("/backups/restore", response_model=RestoreStateOut)
def get_restore(request: Request, _admin: User = Depends(require_admin)) -> RestoreStateOut:
    return _restore_state(request.app.state.settings)


@router.delete("/backups/restore", response_model=RestoreStateOut)
def delete_restore(request: Request, _admin: User = Depends(require_admin)) -> RestoreStateOut:
    """Discard a staged restore and forget the last result."""
    settings = request.app.state.settings
    restore.discard_pending(settings)
    restore.clear_result(settings)
    return _restore_state(settings)


@router.post("/backups/restore/apply", response_model=RestartOut)
def apply_restore(
    request: Request, _admin: User = Depends(require_admin), session: Session = Depends(get_session)
) -> RestartOut:
    """Restart the server; the staged restore is swapped in before anything opens."""
    settings = request.app.state.settings
    if not restore.pending_exists(settings):
        raise ApiError(409, "restore_not_staged", "No restore is staged")
    if maintenance.read(settings) is None:
        raise ApiError(409, "maintenance_required", "Turn maintenance mode on first")
    if job_running(session):
        raise ApiError(409, "job_running", "Another job is already running")
    boot = Path(settings.bootstrap_dir or settings.data_dir).expanduser().resolve()
    return RestartOut(restarting=schedule_api_restart(boot))


class RestoreFromPathIn(BaseModel):
    path: str = Field(default="", max_length=4096, description="A site backup .zip on the server")


def _require_maintenance(request: Request) -> None:
    if maintenance.read(request.app.state.settings) is None:
        raise ApiError(409, "maintenance_required", "Turn maintenance mode on first")


@router.post("/backups/restore", response_model=ImportOut)
def post_restore_from_path(
    body: RestoreFromPathIn, request: Request, admin: User = Depends(require_admin)
) -> ImportOut:
    """Stage a restore from a zip anywhere on the server (a share, a USB drive). Nothing is copied."""
    text = body.path.strip()
    if not text:
        raise ApiError(400, "backup_path_required", "Choose the backup zip on the server")
    _require_maintenance(request)
    try:
        path = Path(text).expanduser().resolve()
        is_file = path.is_file()
    except OSError as exc:
        raise wrapped(400, "backup_invalid", exc) from exc
    if not is_file:
        raise ApiError(400, "backup_not_a_file", "That path is not a file", {"path": text})
    try:
        restore.check_archive(path)
    except (ArchiveError, OSError) as exc:
        raise check_error(exc) from exc
    runner: ImportJobRunner = request.app.state.import_jobs
    try:
        job = runner.start_restore(user_id=admin.id, archive=path)
    except ImportBusy as exc:
        raise _busy_error(exc) from exc
    return _job_out(job)


@router.put("/backups/upload", response_model=BackupOut, openapi_extra=UPLOAD_BODY)
async def upload_backup(
    request: Request, name: str = Query(default="", max_length=255), user: User = Depends(require_user_brief)
) -> BackupOut:
    """Receive a site backup zip from the browser into ``backups/`` (raw body, streamed to disk)."""
    if user.role != "admin":
        raise ApiError(403, "admin_only", "admin only")
    settings = request.app.state.settings
    if restore.pending_exists(settings):
        raise ApiError(409, "restore_pending", "A restore is waiting to be applied")
    session = request.app.state.session_factory()
    try:
        if job_running(session):
            raise ApiError(409, "job_running", "Another job is already running")
    finally:
        session.close()
    final = await receive_archive(
        request,
        settings.backup_dir,
        prefix="mosaicwave-site",
        check=lambda path: restore.check_archive(path, part_only=True),
        file_name=name,
    )
    listed = set_containing(settings.backup_dir, SITE_NAME, final)
    info = _describe(listed) if listed is not None else None
    if info is None:
        raise ApiError(400, "backup_invalid", "The archive could not be read", {"message": "unreadable"})
    return info


@router.post("/backups/{name}/restore", response_model=ImportOut)
def post_restore(name: str, request: Request, admin: User = Depends(require_admin)) -> ImportOut:
    """Stage a restore of this backup. Needs maintenance mode; nothing live changes until Apply."""
    path = _existing(request, name)
    _require_maintenance(request)
    try:
        restore.check_archive(path)
    except (ArchiveError, OSError) as exc:
        raise check_error(exc) from exc
    runner: ImportJobRunner = request.app.state.import_jobs
    try:
        job = runner.start_restore(user_id=admin.id, archive=path)
    except ImportBusy as exc:
        raise _busy_error(exc) from exc
    return _job_out(job)


@router.get("/backups/{name}")
def download_backup(name: str, request: Request, _admin: User = Depends(require_admin)) -> FileResponse:
    path = _existing(request, name)
    return FileResponse(path, media_type="application/zip", filename=name)


@router.delete("/backups/{name}")
def delete_backup(name: str, request: Request, _admin: User = Depends(require_admin)) -> dict[str, bool]:
    delete_set(_existing(request, name))
    return {"ok": True}
