from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from mosaicwave.auth.deps import require_admin
from mosaicwave.config import (
    data_dir_env_override,
    data_dir_occupied,
    ensure_writable_dir,
    read_data_dir_mount,
    read_data_dir_smb,
    schedule_api_restart,
)
from mosaicwave.storage.smbpath import (
    coerce_host_folder,
    last_mount_notice,
    nsmb_username_for_share,
    parse_remote_share,
    prepare_smb_url,
    share_mount_dest,
    smb_url_for_host_path,
)
from mosaicwave.db import get_session
from mosaicwave.library.importjob import ImportBusy, ImportJobRunner
from mosaicwave.models import User
from mosaicwave.runtime import switch_data_dir

router = APIRouter()
_log = logging.getLogger(__name__)


class HostSettingsOut(BaseModel):
    data_dir: str
    default_data_dir: str
    app_dir: str
    data_dir_smb: str | None = None
    data_dir_mount: str | None = None
    smb_user: str | None = None
    env_override: bool
    restarting: bool = False
    notice: str | None = None


class HostSettingsIn(BaseModel):
    data_dir: str
    smb_user: str | None = None
    smb_password: str | None = None
    mount_point: str | None = None


def _bad_request(exc: BaseException) -> HTTPException:
    _log.warning("PUT /settings: %s", exc)
    return HTTPException(status_code=400, detail=str(exc))


def _smb_user_from_settings(smb_url: str | None) -> str | None:
    share = parse_remote_share(smb_url or "")
    if share is None:
        return None
    if share.user:
        return share.user
    user = nsmb_username_for_share(share)
    return user or None


def _out(
    request: Request, *, restarting: bool = False, notice: str | None = None
) -> HostSettingsOut:
    settings = request.app.state.settings
    boot_path = Path(settings.bootstrap_dir or settings.data_dir).expanduser().resolve()
    boot = str(boot_path)
    smb = read_data_dir_smb(boot_path)
    return HostSettingsOut(
        data_dir=str(settings.data_dir.expanduser().resolve()),
        default_data_dir=boot,
        app_dir=boot,
        data_dir_smb=smb,
        data_dir_mount=read_data_dir_mount(boot_path),
        smb_user=_smb_user_from_settings(smb),
        env_override=data_dir_env_override(),
        restarting=restarting,
        notice=notice,
    )


@router.get("/settings", response_model=HostSettingsOut)
def get_settings(request: Request, _admin: User = Depends(require_admin)) -> HostSettingsOut:
    return _out(request)


@router.put("/settings", response_model=HostSettingsOut)
def put_settings(
    request: Request,
    body: HostSettingsIn,
    session: Session = Depends(get_session),
    _admin: User = Depends(require_admin),
) -> HostSettingsOut:
    if data_dir_env_override():
        raise HTTPException(
            status_code=409,
            detail="MOSAICWAVE_DATA_DIR is set; the data folder cannot be changed in Settings",
        )
    typed = (body.data_dir or "").strip()
    smb_user = (body.smb_user or "").strip()
    smb_password = body.smb_password or ""
    mount_point = (body.mount_point or "").strip()
    try:
        dest = coerce_host_folder(
            typed,
            mount=True,
            user=smb_user,
            password=smb_password,
            mount_point=mount_point,
        )
    except ValueError as exc:
        raise _bad_request(exc) from exc
    mount_notice = last_mount_notice() or None
    if not dest.is_absolute():
        raise _bad_request(ValueError("data_dir must be an absolute path"))
    dest = dest.expanduser().resolve()
    src = request.app.state.settings.data_dir.expanduser().resolve()
    boot = Path(request.app.state.settings.bootstrap_dir or src).expanduser().resolve()
    smb = smb_url_for_host_path(prepare_smb_url(typed, user=smb_user)) or (
        smb_url_for_host_path(dest)
    )
    stored_mount: str | None = None
    if dest == boot:
        smb = None
    elif smb:
        stored_mount = share_mount_dest() or mount_point or None
    if dest != src and dest != boot and data_dir_occupied(dest):
        raise HTTPException(
            status_code=409,
            detail="destination already has a mosaicWave library",
        )
    if dest != src:
        try:
            ensure_writable_dir(dest)
        except ValueError as exc:
            raise _bad_request(exc) from exc
    runner: ImportJobRunner = request.app.state.import_jobs
    try:
        with runner.idle_exclusive(session):
            session.close()
            switch_data_dir(request.app, dest, smb_url=smb, mount_point=stored_mount)
    except ImportBusy:
        raise HTTPException(
            status_code=409,
            detail="cannot move the data folder while a job is running",
        ) from None
    except ValueError as exc:
        raise _bad_request(exc) from exc
    except OSError as exc:
        raise _bad_request(OSError(f"could not move the data folder: {exc}")) from exc
    # Keep a live SMB mount. Killing the API here remounts from config and, when
    # that fails, leaves Settings on the app folder. Local-folder moves still restart.
    restarting = False if smb else schedule_api_restart(boot)
    return _out(request, restarting=restarting, notice=mount_notice)
