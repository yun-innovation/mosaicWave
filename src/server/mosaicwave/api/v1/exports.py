"""The signed-in user's exports: ordinary files made from their own library (doc/backup.md).

An export is a folder under ``exports/{user_id}/``, so a user only ever sees their own. It holds zip
parts plus any file too big to zip; each file downloads on its own.
"""

from __future__ import annotations

import mimetypes
from pathlib import PurePosixPath
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from mosaicwave import export
from mosaicwave.api.errors import ApiError
from mosaicwave.api.v1.library import ImportOut, _busy_error, _job_out
from mosaicwave.auth.deps import get_library_session, require_user
from mosaicwave.library.importjob import ImportBusy, ImportJobRunner
from mosaicwave.models import Asset, User

router = APIRouter()


class ExportIn(BaseModel):
    layout: Literal["plain", "takeout"] = Field(default="plain", description="Folder layout of the files")
    split_gb: float | None = Field(
        default=None,
        ge=0.0001,
        le=1024,
        description="Part size in GB; a file larger than this is not zipped. Empty: one zip",
    )
    album_ids: list[str] = Field(default_factory=list, max_length=10_000)
    asset_ids: list[str] = Field(default_factory=list, max_length=200_000)
    all: bool = Field(default=False, description="Export the whole library")


class ExportFileOut(BaseModel):
    path: str
    size: int
    zipped: bool


class ExportOut(BaseModel):
    name: str
    created_at: str
    layout: str
    split_bytes: int | None = None
    assets: int
    albums: int
    total_bytes: int
    size: int
    warnings: list[str] = Field(default_factory=list)
    files: list[ExportFileOut] = Field(default_factory=list)


def _folder(request: Request, user: User, name: str):  # type: ignore[no-untyped-def]
    folder = export.export_folder(request.app.state.settings, user.id, name)
    if folder is None:
        raise ApiError(404, "export_not_found", "Export not found", {"name": name})
    return folder


@router.post("/me/exports", response_model=ImportOut)
def post_my_export(
    body: ExportIn,
    request: Request,
    user: User = Depends(require_user),
    session: Session = Depends(get_library_session),
) -> ImportOut:
    """Start an export job: your whole library, some albums, or some photos."""
    album_ids = list(dict.fromkeys(body.album_ids))
    asset_ids = list(dict.fromkeys(body.asset_ids))
    if body.all and (album_ids or asset_ids):
        raise ApiError(400, "export_scope", "Choose the whole library, or albums and photos, not both")
    if not body.all and not album_ids and not asset_ids:
        raise ApiError(400, "export_nothing_selected", "Choose what to export")
    missing = export.missing_albums(session, album_ids)
    if missing:
        raise ApiError(404, "album_not_found", "Album not found", {"id": missing[0]})
    if body.all and session.scalar(select(Asset.id).where(Asset.deleted_at.is_(None)).limit(1)) is None:
        raise ApiError(400, "export_empty", "There is nothing to export")
    runner: ImportJobRunner = request.app.state.import_jobs
    split = int(body.split_gb * 2**30) if body.split_gb else None
    try:
        job = runner.start_export(
            user_id=user.id,
            layout=body.layout,
            split_bytes=split,
            album_ids=album_ids,
            asset_ids=asset_ids,
            everything=body.all,
        )
    except ImportBusy as exc:
        raise _busy_error(exc) from exc
    return _job_out(job)


@router.get("/me/exports", response_model=list[ExportOut])
def get_my_exports(request: Request, user: User = Depends(require_user)) -> list[ExportOut]:
    return [ExportOut(**view) for view in export.list_exports(request.app.state.settings, user.id)]


@router.get("/me/exports/{name}/files/{path:path}")
def download_my_export_file(
    name: str, path: str, request: Request, user: User = Depends(require_user)
) -> FileResponse:
    folder = _folder(request, user, name)
    target = export.export_file_path(folder, path)
    if target is None:
        raise ApiError(404, "export_not_found", "Export not found", {"name": name})
    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(target, media_type=media_type, filename=PurePosixPath(path).name)


@router.delete("/me/exports/{name}")
def delete_my_export(name: str, request: Request, user: User = Depends(require_user)) -> dict[str, bool]:
    export.delete_export(_folder(request, user, name))
    return {"ok": True}
