from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from mosaicwave.api.errors import ApiError, wrapped
from mosaicwave.auth.deps import get_library_session, require_user
from mosaicwave.export import export_user_dir
from mosaicwave.libbackup import user_backup_dir
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
    kind: str = Query(default="takeout", pattern="^(takeout|backup)$"),
    session: Session = Depends(get_library_session),
    user: User = Depends(require_user),
) -> FsListOut:
    """Browse host folders. Listing does not move the library or write config.

    ``kind=takeout`` (default) shows Takeout zips; ``kind=backup`` shows any zip and is admin only
    (it is how the admin picks a site or library backup to restore). Other users' library folders and
    backup folders are never listed.
    """
    if kind == "backup" and user.role != "admin":
        raise ApiError(403, "admin_only", "admin only")
    settings = request.app.state.settings
    existing = user_library(session, user)
    mine = Path(existing.root_uri).resolve() if existing and existing.root_uri else None
    libraries = library_root(settings.data_dir, user.id).parent.resolve()
    try:
        listing = list_host_dir(path, kind)
    except FileNotFoundError as exc:
        raise wrapped(404, "folder_not_found", exc) from exc
    except PermissionError as exc:
        raise wrapped(403, "folder_forbidden", exc) from exc
    except OSError as exc:
        raise wrapped(400, "folder_unreadable", exc) from exc
    current = Path(listing.path).resolve() if listing.path else None
    own_backups = user_backup_dir(settings, user.id).resolve()
    own_exports = export_user_dir(settings, user.id).resolve()
    shared = (
        (libraries, mine),
        (own_backups.parent, own_backups),
        (own_exports.parent, own_exports),
    )
    if current is not None:
        for parent, own_dir in shared:
            if own_dir is None and parent == libraries:
                continue  # no library of one's own yet: nothing to be allowed into
            inside = own_dir is not None and (current == own_dir or _is_under(current, own_dir))
            if _is_under(current, parent) and current != parent and not inside:
                raise ApiError(403, "folder_not_allowed", "not allowed")
    entries = listing.entries
    if current is not None:
        for parent, own_dir in shared:
            if current == parent and own_dir is not None:
                entries = [e for e in entries if Path(e.path).resolve() == own_dir]
    return FsListOut(
        path=listing.path,
        parent=listing.parent,
        entries=[
            FsEntryOut(name=e.name, path=e.path, is_dir=e.is_dir) for e in entries
        ],
        takeout_zip_count=listing.takeout_zip_count,
        warning=listing.warning,
    )
