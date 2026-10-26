"""While the data folder is being moved, library requests wait (milestone 13, doc/maintenance.md)."""

from __future__ import annotations

from fastapi import Request

from mosaicwave.api.errors import ApiError
from mosaicwave.api.v1.maintenance import API_PREFIX, OPEN_PATHS

RETRY_AFTER_SECONDS = "5"
# Site-database endpoints the admin needs to follow the move: who they are, the settings page, the
# status of the running job (and its cancel). Nothing here reads or writes the files being copied.
ALLOWED_PATHS = frozenset({"/api/v1/me", "/api/v1/settings"})
ALLOWED_PREFIXES = ("/api/v1/jobs",)


def is_moving(request: Request) -> bool:
    return bool(getattr(request.app.state, "moving", False))


def moving_guard(request: Request) -> None:
    """App-wide dependency: while a move runs, everything that touches the library answers 503."""
    if not is_moving(request):
        return
    path = request.url.path
    if (
        not path.startswith(API_PREFIX)
        or path in OPEN_PATHS
        or path in ALLOWED_PATHS
        or path.startswith(ALLOWED_PREFIXES)
    ):
        return
    raise ApiError(
        503,
        "data_dir_moving",
        "the library is being moved; try again shortly",
        headers={"Retry-After": RETRY_AFTER_SECONDS},
    )
