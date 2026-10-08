from pathlib import Path
import sys

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from mosaicwave.api.v1.auth import router as auth_router
from mosaicwave.api.v1.health import router as health_router
from mosaicwave.api.v1.fs import router as fs_router
from mosaicwave.api.v1.library import router as library_router
from mosaicwave.api.v1.settings import router as settings_router
from mosaicwave.config import Settings, consume_restart_flag
from mosaicwave.runtime import attach_runtime

DEV_ORIGINS = (
    "http://127.0.0.1:3000",
    "http://localhost:3000",
)


def _mount_web(app: FastAPI, web_root: Path) -> None:
    root = web_root.expanduser().resolve()
    if not root.is_dir():
        return
    index = root / "index.html"

    @app.get("/")
    def _ui_index() -> FileResponse:
        if not index.is_file():
            raise HTTPException(
                status_code=404,
                detail=f"web UI missing ({index})",
            )
        return FileResponse(index, media_type="text/html")

    app.mount("/", StaticFiles(directory=str(root), html=True), name="web")


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        consume_restart_flag()
        settings = Settings.from_env()

    app = FastAPI(title="mosaicWave", version="0.1.0")
    attach_runtime(app, settings, recover=True)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(DEV_ORIGINS),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Accept-Ranges", "Content-Range", "Content-Length"],
    )
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(fs_router, prefix="/api/v1")
    app.include_router(library_router, prefix="/api/v1")
    app.include_router(settings_router, prefix="/api/v1")
    if settings.web_root is not None:
        _mount_web(app, settings.web_root)
    else:

        @app.get("/")
        def _ui_not_configured() -> None:
            raise HTTPException(
                status_code=404,
                detail="web UI not mounted (missing MOSAICWAVE_WEB_ROOT and …/web/index.html)",
            )

    return app


if "pytest" in sys.modules:
    app = FastAPI(title="mosaicWave")
else:
    app = create_app()
