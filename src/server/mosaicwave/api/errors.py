from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from mosaicwave.i18n import detail_for
from mosaicwave.library.dbs import LibraryLocked


class ApiError(HTTPException):
    """HTTP error with a stable ``code`` the web UI translates as ``errors:{code}``.

    ``detail`` stays English for logs and older clients; ``params`` fill ``{{name}}``
    placeholders in the translation.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        detail: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code
        self.params = params or {}


def wrapped(status_code: int, code: str, exc: BaseException) -> ApiError:
    """Category code around a lower-level English message (``params.message``)."""
    message = str(exc) or exc.__class__.__name__
    return ApiError(status_code, code, message, {"message": message})


async def _api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    # ``detail`` is written in the response language (English keeps the server's sentence);
    # ``code`` and ``params`` never change, so a client can still translate on its own.
    detail = detail_for(request, exc.code, exc.params, str(exc.detail))
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": detail, "code": exc.code, "params": exc.params},
        headers=exc.headers,
    )


async def _library_locked_handler(request: Request, _exc: Exception) -> JSONResponse:
    # The user's library is being swapped by a restore; the web UI retries shortly.
    code = "library_restoring"
    english = "your library is being restored; try again in a moment"
    return JSONResponse(
        status_code=503,
        content={
            "detail": detail_for(request, code, {}, english),
            "code": code,
            "params": {},
        },
        headers={"Retry-After": "2"},
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error_handler)
    app.add_exception_handler(LibraryLocked, _library_locked_handler)
