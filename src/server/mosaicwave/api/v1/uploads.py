"""Resumable upload for the mobile apps. Design: doc/mobile.md § Upload design."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from mosaicwave.api.errors import ApiError
from mosaicwave.auth.deps import get_library_session, require_user, require_user_brief
from mosaicwave.library import uploads
from mosaicwave.library.ownership import ensure_user_library
from mosaicwave.library.query import AssetOut, asset_to_out
from mosaicwave.models import User

router = APIRouter(tags=["uploads"])

OCTETS = "application/offset+octet-stream"


class CheckItem(BaseModel):
    content_hash: str = Field(description="SHA-256 of the file, hex.")
    size: int = Field(ge=0, description="File size in bytes (not used to decide; kept for the app's own log).")


class CheckIn(BaseModel):
    items: list[CheckItem] = Field(max_length=uploads.CHECK_MAX)


class CheckOut(BaseModel):
    known: list[str] = Field(description="Hashes the library already has: do not upload.")
    removed: list[str] = Field(description="Hashes of photos the user deleted on the server: do not upload again.")


class UploadIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(ge=1, description="Total bytes.")
    content_hash: str = Field(description="SHA-256 of the whole file, hex. Verified when the last byte arrives.")
    taken_at: datetime | None = Field(default=None, description="Capture time the app knows; used only if the file has none.")
    mime: str | None = None


class UploadOut(BaseModel):
    id: str | None = None
    state: Literal["open", "done", "exists"]
    offset: int = 0
    size: int = 0
    chunk_size: int = uploads.CHUNK_SIZE
    expires_at: str | None = None
    asset_id: str | None = None
    asset: AssetOut | None = None


def _refuse(exc: uploads.UploadError) -> ApiError:
    return ApiError(exc.status, exc.code, exc.message, exc.params)


def _folder(request: Request, user: User):
    return uploads.user_dir(request.app.state.settings, user.id)


def _open_out(folder, meta: uploads.UploadMeta) -> UploadOut:
    return UploadOut(
        id=meta.id,
        state="open",
        offset=uploads.offset_of(folder, meta.id),
        size=meta.size,
        expires_at=meta.expires_at,
    )


def _headers(out: UploadOut) -> dict[str, str]:
    return {"Upload-Offset": str(out.offset), "Upload-Length": str(out.size), "Cache-Control": "no-store"}


@router.post("/uploads/check", response_model=CheckOut)
def check_uploads(
    body: CheckIn,
    user: User = Depends(require_user),
    session: Session = Depends(get_library_session),
) -> CheckOut:
    try:
        hashes = [uploads.normalize_hash(item.content_hash) for item in body.items]
    except uploads.UploadError as exc:
        raise _refuse(exc) from exc
    known, removed = uploads.known_hashes(session, hashes)
    return CheckOut(
        known=[h for h in dict.fromkeys(hashes) if h in known],
        removed=[h for h in dict.fromkeys(hashes) if h in removed],
    )


@router.post("/uploads", response_model=UploadOut, status_code=201)
def create_upload(
    body: UploadIn,
    request: Request,
    response: Response,
    user: User = Depends(require_user),
    session: Session = Depends(get_library_session),
) -> UploadOut:
    try:
        digest = uploads.normalize_hash(body.content_hash)
        have = uploads.live_asset_with_hash(session, digest)
        if have is not None:
            response.status_code = 200
            return UploadOut(state="exists", asset_id=have.id, size=body.size)
        meta, created = uploads.create(
            request.app.state.settings,
            user.id,
            filename=body.filename,
            size=body.size,
            content_hash=digest,
            taken_at=body.taken_at,
            mime=body.mime,
        )
    except uploads.UploadError as exc:
        raise _refuse(exc) from exc
    response.status_code = 201 if created else 200
    return _open_out(_folder(request, user), meta)


def _load(request: Request, user: User, upload_id: str):
    folder = _folder(request, user)
    try:
        return folder, uploads.load(folder, upload_id)
    except uploads.UploadError as exc:
        raise _refuse(exc) from exc


@router.head("/uploads/{upload_id}", status_code=204)
def head_upload(upload_id: str, request: Request, user: User = Depends(require_user)) -> Response:
    folder, meta = _load(request, user, upload_id)
    return Response(status_code=204, headers=_headers(_open_out(folder, meta)))


@router.get("/uploads/{upload_id}", response_model=UploadOut)
def get_upload(upload_id: str, request: Request, response: Response, user: User = Depends(require_user)) -> UploadOut:
    folder, meta = _load(request, user, upload_id)
    out = _open_out(folder, meta)
    response.headers.update(_headers(out))
    return out


@router.delete("/uploads/{upload_id}", status_code=204)
def abort_upload(upload_id: str, request: Request, user: User = Depends(require_user)) -> Response:
    folder, meta = _load(request, user, upload_id)
    try:
        with uploads.busy(meta.id):
            uploads.discard(folder, meta.id)
    except uploads.UploadError as exc:
        raise _refuse(exc) from exc
    return Response(status_code=204)


def _finish_sync(request: Request, user: User, folder, meta: uploads.UploadMeta) -> UploadOut:
    session = request.app.state.library_dbs.factory(user.id)()
    try:
        source = ensure_user_library(session, user, request.app.state.settings.data_dir)
        state, asset = uploads.finish(session, source, folder, meta)
        session.commit()
        return UploadOut(
            id=meta.id,
            state=state,  # type: ignore[arg-type]
            offset=meta.size,
            size=meta.size,
            asset_id=asset.id,
            asset=asset_to_out(asset),
        )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@router.patch(
    "/uploads/{upload_id}",
    response_model=UploadOut,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {OCTETS: {"schema": {"type": "string", "format": "binary"}}},
            "description": "The next bytes. Header `Upload-Offset` must equal the offset the server holds.",
        }
    },
)
async def patch_upload(
    upload_id: str,
    request: Request,
    response: Response,
    user: User = Depends(require_user_brief),
) -> UploadOut:
    folder, meta = _load(request, user, upload_id)
    try:
        raw = request.headers.get("upload-offset", "")
        if not raw.isdigit():
            raise uploads.UploadError(400, "upload_offset_missing", "header Upload-Offset (bytes) is required")
        want = int(raw)
        with uploads.busy(meta.id):
            have = uploads.offset_of(folder, meta.id)
            if want != have:
                raise uploads.UploadError(409, "upload_offset_mismatch", "wrong offset", {"offset": have})
            declared = request.headers.get("content-length", "")
            if declared.isdigit() and have + int(declared) > meta.size:
                raise uploads.UploadError(400, "upload_too_long", "more bytes than the declared size", {"size": meta.size})
            written = await uploads.append(folder, meta, have, request.stream())
            uploads.touch(folder, meta)
            if have + written < meta.size:
                out = _open_out(folder, meta)
                response.headers.update(_headers(out))
                return out
            out = await run_in_threadpool(_finish_sync, request, user, folder, meta)
            response.headers.update(_headers(out))
            return out
    except uploads.UploadError as exc:
        raise _refuse(exc) from exc
