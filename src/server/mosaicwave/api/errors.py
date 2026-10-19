from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse


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


async def _api_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "code": exc.code, "params": exc.params},
        headers=exc.headers,
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error_handler)
