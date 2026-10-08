from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from mosaicwave.auth.deps import require_user
from mosaicwave.db import get_session
from mosaicwave.library.fsbrowse import list_host_dir
from mosaicwave.library.ownership import library_root, user_library
from mosaicwave.models import User

router = APIRouter()


class FsEntryOut(BaseModel):
    name: str
    path: str
    is_dir: bool


class FsListOut(BaseModel):
    path: str
    parent: str | None
    entries: list[FsEntryOut]
    takeout_zip_count: int
    warning: str | None = None


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


@router.get("/fs/list", response_model=FsListOut)
def fs_list(
    request: Request,
    path: str | None = Query(default=None),
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> FsListOut:
    """Browse host folders. Listing does not move the library or write config."""
    settings = request.app.state.settings
    existing = user_library(session, user)
    mine = Path(existing.root_uri).resolve() if existing and existing.root_uri else None
    libraries = library_root(settings.data_dir, user.id).parent.resolve()
    try:
        listing = list_host_dir(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    current = Path(listing.path).resolve() if listing.path else None
    if current is not None and mine is not None:
        under_libs = _is_under(current, libraries)
        own = current == mine or _is_under(current, mine)
        if under_libs and current != libraries and not own:
            raise HTTPException(status_code=403, detail="not allowed")
    entries = listing.entries
    if current is not None and mine is not None and current == libraries:
        entries = [e for e in entries if Path(e.path).resolve() == mine]
    return FsListOut(
        path=listing.path,
        parent=listing.parent,
        entries=[
            FsEntryOut(name=e.name, path=e.path, is_dir=e.is_dir) for e in entries
        ],
        takeout_zip_count=listing.takeout_zip_count,
        warning=listing.warning,
    )
