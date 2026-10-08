from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from starlette.requests import ClientDisconnect
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mosaicwave.auth.access import (
    can_read_source,
    can_write_source,
    grant_perm,
    granted_source_ids,
    list_live_grants,
    replace_source_grants,
)
from mosaicwave.auth.deps import require_user, require_user_brief
from mosaicwave.config import Settings
from mosaicwave.db import get_session, sqlite_locked
from mosaicwave.library.dump import TakeoutInspect, inspect_takeout_path
from mosaicwave.library.importjob import (
    ImportBusy,
    ImportJobRunner,
    job_view,
    list_job_events,
    list_jobs,
)
from mosaicwave.library.albums import (
    AlbumConflict,
    AlbumStale,
    add_assets,
    album_view,
    check_base_rev,
    create_album,
    delete_album,
    get_visible_album,
    list_albums,
    patch_album,
    remove_asset,
)
from mosaicwave.library.media import is_media
from mosaicwave.library.query import AssetListOut, AssetOut, asset_to_out, list_assets
from mosaicwave.library.sync import SyncChangesOut, pull_changes
from mosaicwave.library.serve import original_file_response
from mosaicwave.library.ownership import ensure_user_library
from mosaicwave.library.takeout import (
    filestore_for_source,
    index_media_file,
    scan_folder,
    unused_relative,
)
from mosaicwave.library.thumbs import (
    _broken_placeholder_jpeg,
    ensure_preview,
    ensure_thumb,
)
from mosaicwave.models import Asset, Job, Source, User
from mosaicwave.storage.blobstore import BlobStore
from mosaicwave.library.takeoutpush import MAX_PUSH_BYTES, write_upload_async
from mosaicwave.storage.errors import InvalidPath, NotSupported
from mosaicwave.storage.errors import NotFound as StoreNotFound

MAX_UPLOAD_BYTES = 512 * 1024 * 1024

router = APIRouter()


class TakeoutPushStartIn(BaseModel):
    file_count: int | None = Field(default=None, ge=1, le=1_000_000)


class TakeoutImportIn(BaseModel):
    path: str | None = Field(default=None, description="Takeout download or extracted folder")
    dest: str | None = Field(
        default=None,
        description="Ignored. Import always writes into the caller's private library.",
    )


class DumpWarningOut(BaseModel):
    code: str
    severity: str
    message: str
    path: str | None = None


class ZipSeriesOut(BaseModel):
    stamp: str
    series: str
    parts: list[int]
    missing_parts: list[int]


class LooseMediaOut(BaseModel):
    name: str
    size: int
    part: int | None
    original_name: str | None


class TakeoutInspectOut(BaseModel):
    path: str
    kind: str
    extract_root: str | None
    photos_root: str | None
    import_root: str | None
    zip_count: int
    series: list[ZipSeriesOut]
    loose_media: list[LooseMediaOut]
    sidecar_without_media_count: int
    sidecar_without_media: list[str]
    album_metadata_count: int
    ready: bool
    warnings: list[DumpWarningOut]


class SourceOut(BaseModel):
    id: str
    kind: str
    backend: str
    last_scan_at: str | None
    error: str | None
    asset_count: int = 0
    perm: str | None = None
    owned: bool = False


class GrantOut(BaseModel):
    user_id: str
    username: str
    display_name: str
    perm: str


class GrantIn(BaseModel):
    user_id: str
    perm: str = "read"


class GrantsPutIn(BaseModel):
    grants: list[GrantIn]


class AlbumOut(BaseModel):
    id: str
    source_id: str | None = None
    name: str
    cover_asset_id: str | None = None
    sort: int = 0
    rev: int = 0
    asset_count: int = 0
    updated_at: str | None = None


class AlbumCreateIn(BaseModel):
    name: str
    id: str | None = Field(default=None, description="Replica-created UUID; omitted on the live web UI")


class AlbumPatchIn(BaseModel):
    name: str | None = None
    cover_asset_id: str | None = None
    base_rev: int | None = Field(default=None, ge=0, description="Replica push: 409 if server rev differs")


class AlbumAssetsIn(BaseModel):
    asset_ids: list[str] = Field(min_length=1, max_length=200)
    base_rev: int | None = Field(default=None, ge=0, description="Replica push: 409 if server rev differs")


class ImportOut(BaseModel):
    id: str | None = None
    kind: str | None = None
    status: str
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    path: str | None = None
    processed: int = 0
    total: int = 0
    current: str | None = None
    source_id: str | None = None
    assets: int = 0
    albums: int = 0
    memberships: int = 0
    import_root: str | None = None
    mode: str | None = None
    phase: str | None = None
    error: str | None = None
    warnings: list[DumpWarningOut] = Field(default_factory=list)


class JobEventOut(BaseModel):
    id: str
    at: str
    level: str
    message: str


class JobListOut(BaseModel):
    items: list[ImportOut]


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _own_library(request: Request, session: Session, user: User) -> Source:
    return ensure_user_library(session, user, _settings(request).data_dir)


def _library_dest(request: Request, user: User) -> Path:
    factory = request.app.state.session_factory
    session = factory()
    try:
        lib = _own_library(request, session, user)
        dest = Path(lib.root_uri)
        dest.mkdir(parents=True, exist_ok=True)
        session.commit()
        return dest
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _readable_ids(session: Session, user: User) -> set[str]:
    return granted_source_ids(session, user)


def _writable_ids(session: Session, user: User) -> set[str]:
    return granted_source_ids(session, user, write=True)


def _owned_source(session: Session, user: User, source_id: str) -> Source:
    source = _live_source(session, source_id)
    if source.owner_user_id != user.id:
        raise HTTPException(status_code=404, detail="source not found")
    return source


def _grant_out(session: Session, row) -> GrantOut:
    target = session.get(User, row.user_id)
    return GrantOut(
        user_id=row.user_id,
        username=target.provider_subject if target is not None else "",
        display_name=target.display_name if target is not None else "",
        perm=row.perm,
    )


def _job_owned(row: Job, user: User) -> bool:
    return row.user_id == user.id


def _takeout_raw_path(request: Request, body: TakeoutImportIn | None) -> str:
    settings = _settings(request)
    raw = (body.path if body else None) or (
        str(settings.takeout_dir) if settings.takeout_dir else None
    )
    if not raw:
        raise HTTPException(
            status_code=400,
            detail="path is required (or set MOSAICWAVE_TAKEOUT_DIR)",
        )
    return raw


def _inspect_dir(raw: str) -> TakeoutInspect:
    root = Path(raw).expanduser()
    if not root.is_dir():
        raise HTTPException(status_code=400, detail="path is not a directory")
    return inspect_takeout_path(root)


def _inspect_out(report: TakeoutInspect) -> TakeoutInspectOut:
    return TakeoutInspectOut(
        path=report.path,
        kind=report.kind,
        extract_root=report.extract_root,
        photos_root=report.photos_root,
        import_root=report.import_root,
        zip_count=report.zip_count,
        series=[
            ZipSeriesOut(
                stamp=s.stamp,
                series=s.series,
                parts=s.parts,
                missing_parts=s.missing_parts,
            )
            for s in report.series
        ],
        loose_media=[
            LooseMediaOut(
                name=m.name,
                size=m.size,
                part=m.part,
                original_name=m.original_name,
            )
            for m in report.loose_media
        ],
        sidecar_without_media_count=report.sidecar_without_media_count,
        sidecar_without_media=report.sidecar_without_media,
        album_metadata_count=report.album_metadata_count,
        ready=report.ready,
        warnings=[
            DumpWarningOut(
                code=w.code, severity=w.severity, message=w.message, path=w.path
            )
            for w in report.warnings
        ],
    )


def _warning_outs(raw: list) -> list[DumpWarningOut]:
    out: list[DumpWarningOut] = []
    for w in raw:
        if isinstance(w, dict):
            path = w.get("path")
            out.append(
                DumpWarningOut(
                    code=str(w.get("code") or ""),
                    severity=str(w.get("severity") or "warning"),
                    message=str(w.get("message") or ""),
                    path=path if isinstance(path, str) else None,
                )
            )
            continue
        out.append(
            DumpWarningOut(code=w.code, severity=w.severity, message=w.message, path=w.path)
        )
    return out


def _job_out(view: dict) -> ImportOut:
    return ImportOut(
        id=view.get("id"),
        kind=view.get("kind"),
        status=view["status"],
        created_at=view.get("created_at"),
        started_at=view.get("started_at"),
        finished_at=view.get("finished_at"),
        path=view.get("path"),
        processed=int(view.get("processed") or 0),
        total=int(view.get("total") or 0),
        current=view.get("current"),
        source_id=view.get("source_id"),
        assets=int(view.get("assets") or 0),
        albums=int(view.get("albums") or 0),
        memberships=int(view.get("memberships") or 0),
        import_root=view.get("import_root"),
        mode=view.get("mode"),
        phase=view.get("phase"),
        error=view.get("error"),
        warnings=_warning_outs(view.get("warnings") or []),
    )


def _source_out(session: Session, row: Source, *, perm: str | None = None, owned: bool = False) -> SourceOut:
    return SourceOut(
        id=row.id,
        kind=row.kind,
        backend=row.backend,
        last_scan_at=row.last_scan_at.isoformat() if row.last_scan_at else None,
        error=row.error,
        asset_count=_count_live(session, row.id),
        perm=perm,
        owned=owned,
    )


def _live_source(session: Session, source_id: str) -> Source:
    source = session.get(Source, source_id)
    if source is None or source.deleted_at is not None:
        raise HTTPException(status_code=404, detail="source not found")
    return source


def _live_asset(session: Session, asset_id: str, user: User) -> tuple[Asset, Source]:
    asset = session.get(Asset, asset_id)
    if asset is None or asset.deleted_at is not None:
        raise HTTPException(status_code=404, detail="asset not found")
    source = session.get(Source, asset.source_id)
    if source is None or source.deleted_at is not None:
        raise HTTPException(status_code=404, detail="asset not found")
    if not can_read_source(session, user, source.id):
        raise HTTPException(status_code=404, detail="asset not found")
    return asset, source


def _count_live(session: Session, source_id: str) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(Asset).where(
                Asset.source_id == source_id, Asset.deleted_at.is_(None)
            )
        )
        or 0
    )


@router.get("/assets", response_model=AssetListOut)
def get_assets(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    album_id: str | None = None,
) -> AssetListOut:
    _own_library(request, session, user)
    ids = _readable_ids(session, user)
    if album_id:
        try:
            get_visible_album(session, album_id=album_id, source_ids=ids)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        return list_assets(
            session,
            limit=limit,
            cursor=cursor,
            source_ids=ids,
            album_id=album_id,
        )
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid cursor: {exc}") from exc


def _album_out(session: Session, row) -> AlbumOut:
    return AlbumOut(**album_view(session, row))


def _album_http_error(exc: Exception) -> None:
    if isinstance(exc, AlbumStale):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, AlbumConflict):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, FileNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise


@router.get("/albums", response_model=list[AlbumOut])
def get_albums(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> list[AlbumOut]:
    _own_library(request, session, user)
    return [AlbumOut(**row) for row in list_albums(session, source_ids=_readable_ids(session, user))]


@router.post("/albums", response_model=AlbumOut)
def post_album(
    body: AlbumCreateIn,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> AlbumOut:
    lib = _own_library(request, session, user)
    try:
        row = create_album(session, source_id=lib.id, name=body.name, album_id=body.id)
    except Exception as exc:
        _album_http_error(exc)
        raise
    session.commit()
    session.refresh(row)
    return _album_out(session, row)


@router.get("/albums/{album_id}", response_model=AlbumOut)
def get_album_one(
    album_id: str,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> AlbumOut:
    _own_library(request, session, user)
    try:
        row = get_visible_album(session, album_id=album_id, source_ids=_readable_ids(session, user))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _album_out(session, row)


@router.patch("/albums/{album_id}", response_model=AlbumOut)
def patch_album_one(
    album_id: str,
    body: AlbumPatchIn,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> AlbumOut:
    _own_library(request, session, user)
    try:
        row = get_visible_album(session, album_id=album_id, source_ids=_writable_ids(session, user))
        check_base_rev(row, body.base_rev)
        if body.name is None and body.cover_asset_id is None:
            raise ValueError("name or cover_asset_id is required")
        row = patch_album(session, row, name=body.name, cover_asset_id=body.cover_asset_id)
    except Exception as exc:
        _album_http_error(exc)
        raise
    session.commit()
    session.refresh(row)
    return _album_out(session, row)


@router.delete("/albums/{album_id}")
def delete_album_one(
    album_id: str,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
    base_rev: int | None = Query(default=None, ge=0),
) -> dict:
    _own_library(request, session, user)
    try:
        row = get_visible_album(session, album_id=album_id, source_ids=_writable_ids(session, user))
        check_base_rev(row, base_rev)
        delete_album(session, row)
    except Exception as exc:
        _album_http_error(exc)
        raise
    session.commit()
    return {"ok": True}


@router.post("/albums/{album_id}/assets", response_model=AlbumOut)
def post_album_assets(
    album_id: str,
    body: AlbumAssetsIn,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> AlbumOut:
    _own_library(request, session, user)
    try:
        row = get_visible_album(session, album_id=album_id, source_ids=_writable_ids(session, user))
        check_base_rev(row, body.base_rev)
        assets = []
        for asset_id in body.asset_ids:
            asset, _source = _live_asset(session, asset_id, user)
            assets.append(asset)
        add_assets(session, row, assets)
    except Exception as exc:
        _album_http_error(exc)
        raise
    session.commit()
    session.refresh(row)
    return _album_out(session, row)


@router.delete("/albums/{album_id}/assets/{asset_id}")
def delete_album_asset(
    album_id: str,
    asset_id: str,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
    base_rev: int | None = Query(default=None, ge=0),
) -> dict:
    _own_library(request, session, user)
    try:
        row = get_visible_album(session, album_id=album_id, source_ids=_writable_ids(session, user))
        check_base_rev(row, base_rev)
        remove_asset(session, row, asset_id)
    except Exception as exc:
        _album_http_error(exc)
        raise
    session.commit()
    return {"ok": True}


@router.get("/sync/changes", response_model=SyncChangesOut)
def get_sync_changes(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
    since_rev: int = Query(0, ge=0),
    cursor: str | None = None,
    limit: int = Query(200, ge=1, le=500),
) -> SyncChangesOut:
    _own_library(request, session, user)
    try:
        return pull_changes(
            session,
            since_rev=since_rev,
            cursor=cursor,
            limit=limit,
            source_ids=granted_source_ids(session, user),
        )
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid cursor: {exc}") from exc


@router.get("/assets/{asset_id}", response_model=AssetOut)
def get_asset(
    asset_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> AssetOut:
    asset, _source = _live_asset(session, asset_id, user)
    return asset_to_out(asset)


_THUMB_RETRY_CACHE = {"Cache-Control": "private, max-age=0, must-revalidate"}


@router.get("/assets/{asset_id}/thumb")
def get_asset_thumb(
    asset_id: str,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> Response:
    asset, source = _live_asset(session, asset_id, user)
    blobs: BlobStore = request.app.state.blob_store
    key = ""
    try:
        key = ensure_thumb(session, blobs, asset, source)
        session.commit()
    except Exception as exc:
        if not sqlite_locked(exc):
            raise
        if session.is_active:
            session.rollback()
        if key:
            try:
                with blobs.get(key) as handle:
                    data = handle.read()
                return Response(
                    content=data, media_type="image/jpeg", headers=_THUMB_RETRY_CACHE
                )
            except StoreNotFound:
                pass
        return Response(
            content=_broken_placeholder_jpeg(),
            media_type="image/jpeg",
            headers=_THUMB_RETRY_CACHE,
        )
    try:
        with blobs.get(key) as handle:
            data = handle.read()
    except StoreNotFound as exc:
        raise HTTPException(status_code=404, detail="thumb not found") from exc
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get("/assets/{asset_id}/preview")
def get_asset_preview(
    asset_id: str,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> Response:
    asset, source = _live_asset(session, asset_id, user)
    blobs: BlobStore = request.app.state.blob_store
    key = ensure_preview(session, blobs, asset, source)
    session.flush()
    try:
        with blobs.get(key) as handle:
            data = handle.read()
    except StoreNotFound as exc:
        raise HTTPException(status_code=404, detail="preview not found") from exc
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get("/assets/{asset_id}/file")
def get_asset_file(
    asset_id: str,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
    download: bool = Query(False),
) -> Response:
    asset, source = _live_asset(session, asset_id, user)
    rel = asset.relative_path
    mime = asset.mime or "application/octet-stream"
    store = filestore_for_source(source)
    return original_file_response(
        store,
        rel,
        mime=mime,
        size=int(asset.size or 0),
        range_header=request.headers.get("range"),
        download=download,
    )


@router.post("/assets", response_model=AssetOut)
def post_asset(
    source_id: str = Form(...),
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> AssetOut:
    source = _live_source(session, source_id)
    if not can_write_source(session, user, source.id):
        raise HTTPException(status_code=404, detail="source not found")
    if source.kind != "folder":
        raise HTTPException(status_code=400, detail="uploads are only supported for folder sources")
    name = Path(file.filename or "upload").name
    if not name or name in (".", ".."):
        raise HTTPException(status_code=400, detail="invalid filename")
    if not is_media(name):
        raise HTTPException(status_code=400, detail="unsupported media type")
    store = filestore_for_source(source)
    rel = unused_relative(store, name)
    written = 0
    try:
        with store.open_write(rel) as dest:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="file too large")
                dest.write(chunk)
    except NotSupported as exc:
        raise HTTPException(status_code=400, detail="source is read-only") from exc
    except HTTPException:
        try:
            store.delete(rel)
        except StoreNotFound:
            pass
        raise
    asset = index_media_file(session, source, store, rel)
    return asset_to_out(asset)


@router.post("/thumbs/generate", response_model=ImportOut)
def post_thumbs_generate(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> ImportOut:
    lib = _own_library(request, session, user)
    runner: ImportJobRunner = request.app.state.import_jobs
    try:
        job = runner.start_thumbs(user_id=user.id, source_id=lib.id)
    except ImportBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _job_out(job)


@router.get("/sources", response_model=list[SourceOut])
def get_sources(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> list[SourceOut]:
    lib = _own_library(request, session, user)
    out = [_source_out(session, lib, perm="write", owned=True)]
    for sid in sorted(_readable_ids(session, user)):
        if sid == lib.id:
            continue
        source = session.get(Source, sid)
        if source is None or source.deleted_at is not None:
            continue
        out.append(
            _source_out(
                session,
                source,
                perm=grant_perm(session, user, source.id),
                owned=False,
            )
        )
    return out


@router.post("/sources", response_model=SourceOut)
def create_source(
    _user: User = Depends(require_user),
) -> SourceOut:
    raise HTTPException(
        status_code=409,
        detail="each user already has one private library",
    )


@router.delete("/sources/{source_id}")
def delete_source(
    source_id: str,
    _user: User = Depends(require_user),
) -> dict[str, str]:
    del source_id
    raise HTTPException(status_code=400, detail="cannot delete your library")


@router.get("/sources/{source_id}/grants", response_model=list[GrantOut])
def get_source_grants(
    source_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> list[GrantOut]:
    source = _owned_source(session, user, source_id)
    return [_grant_out(session, row) for row in list_live_grants(session, source.id)]


@router.put("/sources/{source_id}/grants", response_model=list[GrantOut])
def put_source_grants(
    source_id: str,
    body: GrantsPutIn,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> list[GrantOut]:
    source = _owned_source(session, user, source_id)
    try:
        rows = replace_source_grants(
            session,
            source,
            [(item.user_id, item.perm) for item in body.grants],
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return [_grant_out(session, row) for row in rows]


@router.post("/sources/{source_id}/scan", response_model=SourceOut)
def scan_source(
    request: Request,
    source_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> SourceOut:
    lib = _own_library(request, session, user)
    if source_id != lib.id:
        raise HTTPException(status_code=404, detail="source not found")
    source = scan_folder(session, Path(lib.root_uri), owner_user_id=user.id)
    session.flush()
    return _source_out(session, source, perm="write", owned=True)


@router.post("/import/takeout/inspect", response_model=TakeoutInspectOut)
def inspect_takeout(
    request: Request,
    body: TakeoutImportIn | None = None,
    _user: User = Depends(require_user),
) -> TakeoutInspectOut:
    raw = _takeout_raw_path(request, body)
    return _inspect_out(_inspect_dir(raw))


@router.get("/import/takeout/status", response_model=ImportOut)
def get_takeout_status(
    request: Request,
    user: User = Depends(require_user),
) -> ImportOut:
    runner: ImportJobRunner = request.app.state.import_jobs
    return _job_out(runner.latest_takeout(user.id))


@router.get("/jobs", response_model=JobListOut)
def get_jobs(
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
    kind: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
) -> JobListOut:
    rows = list_jobs(session, kind=kind, limit=limit, user_id=user.id)
    return JobListOut(items=[_job_out(job_view(row)) for row in rows])


@router.get("/jobs/{job_id}", response_model=ImportOut)
def get_job(
    job_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> ImportOut:
    row = session.get(Job, job_id)
    if row is None or not _job_owned(row, user):
        raise HTTPException(status_code=404, detail="job not found")
    return _job_out(job_view(row))


@router.get("/jobs/{job_id}/events", response_model=list[JobEventOut])
def get_job_events(
    job_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> list[JobEventOut]:
    row = session.get(Job, job_id)
    if row is None or not _job_owned(row, user):
        raise HTTPException(status_code=404, detail="job not found")
    return [
        JobEventOut(
            id=ev.id,
            at=ev.at.isoformat() if ev.at else "",
            level=ev.level,
            message=ev.message,
        )
        for ev in list_job_events(session, job_id)
    ]


@router.post("/import/takeout", response_model=ImportOut)
def post_takeout(
    request: Request,
    body: TakeoutImportIn | None = None,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> ImportOut:
    raw = _takeout_raw_path(request, body)
    report = _inspect_dir(raw)
    if not report.import_root:
        raise HTTPException(
            status_code=400,
            detail="no photos found (need takeout-*.zip archives or an extracted Google Photos folder)",
        )
    lib = _own_library(request, session, user)
    dest = Path(lib.root_uri)
    dest.mkdir(parents=True, exist_ok=True)
    runner: ImportJobRunner = request.app.state.import_jobs
    try:
        job = runner.start(Path(raw).expanduser(), report, dest, user_id=user.id)
    except ImportBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _job_out(job)


_log = logging.getLogger("mosaicwave")


def _push_http_error(exc: Exception) -> None:
    if isinstance(exc, ClientDisconnect):
        raise HTTPException(status_code=400, detail="upload interrupted") from exc
    if isinstance(exc, ImportBusy):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, FileNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (ValueError, InvalidPath, OSError)):
        _log.warning("takeout push rejected: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _log.exception("takeout push failed")
    raise HTTPException(status_code=500, detail=str(exc) or exc.__class__.__name__) from exc


def _declared_size(request: Request) -> int | None:
    raw = request.headers.get("content-length")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


@router.post("/import/takeout/push", response_model=ImportOut)
def start_takeout_push(
    request: Request,
    body: TakeoutPushStartIn | None = None,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> ImportOut:
    _own_library(request, session, user)
    runner: ImportJobRunner = request.app.state.import_jobs
    count = body.file_count if body else None
    try:
        job = runner.start_push(
            user_id=user.id, data_dir=_settings(request).data_dir, file_count=count
        )
    except ImportBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _job_out(job)


_PUSH_FILE_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "application/octet-stream": {
                "schema": {"type": "string", "format": "binary"}
            }
        },
    }
}


@router.post(
    "/import/takeout/push/{job_id}/file",
    response_model=ImportOut,
    openapi_extra=_PUSH_FILE_BODY,
)
@router.put(
    "/import/takeout/push/{job_id}/file",
    response_model=ImportOut,
    openapi_extra=_PUSH_FILE_BODY,
)
async def put_takeout_push_file(
    request: Request,
    job_id: str,
    relative_path: str = Query(..., min_length=1, max_length=4096),
    user: User = Depends(require_user_brief),
) -> ImportOut:
    runner: ImportJobRunner = request.app.state.import_jobs
    try:
        declared = _declared_size(request)
        if declared is not None and declared > MAX_PUSH_BYTES:
            raise ValueError(
                f"file too large ({declared} bytes; max is {MAX_PUSH_BYTES})"
            )
        dest, existed = runner.prepare_push_file(
            job_id, user_id=user.id, relative_path=relative_path
        )
        try:
            await write_upload_async(dest, request.stream())
        except OSError as exc:
            raise ValueError(f"could not save {relative_path}: {exc}") from exc
        job = runner.commit_push_file(
            job_id, user_id=user.id, relative_path=relative_path, existed=existed
        )
    except Exception as exc:
        _push_http_error(exc)
        raise
    return _job_out(job)


@router.post("/import/takeout/push/{job_id}/finish", response_model=ImportOut)
def finish_takeout_push(
    request: Request,
    job_id: str,
    user: User = Depends(require_user_brief),
) -> ImportOut:
    dest = _library_dest(request, user)
    runner: ImportJobRunner = request.app.state.import_jobs
    try:
        job = runner.finish_push(job_id, user_id=user.id, dest_root=dest)
    except Exception as exc:
        _push_http_error(exc)
        raise
    return _job_out(job)


@router.post("/import/takeout/push/{job_id}/cancel", response_model=ImportOut)
def cancel_takeout_push(
    request: Request,
    job_id: str,
    user: User = Depends(require_user),
) -> ImportOut:
    runner: ImportJobRunner = request.app.state.import_jobs
    try:
        job = runner.cancel_push(job_id, user_id=user.id)
    except Exception as exc:
        _push_http_error(exc)
        raise
    return _job_out(job)

