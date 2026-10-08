from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from sqlalchemy.orm import sessionmaker

from mosaicwave.config import (
    Settings,
    _delete_named,
    _rewrite_source_root_uris,
    clear_bootstrap_library_payload,
    copy_data_dir,
    session_secret_for,
    with_data_dir,
    write_data_dir_config,
)
from mosaicwave.db import make_engine
from mosaicwave.library.importjob import ImportJobRunner, recover_interrupted_jobs
from mosaicwave.storage.blobstore import LocalBlobStore


def attach_runtime(app: FastAPI, settings: Settings, *, recover: bool = False) -> None:
    engine = make_engine(settings)
    factory = sessionmaker(engine, expire_on_commit=False)
    blobs = LocalBlobStore(settings.blob_dir)
    app.state.settings = settings
    app.state.session_secret = session_secret_for(
        Path(settings.bootstrap_dir or settings.data_dir),
        settings.session_secret,
        data_dir=settings.data_dir,
    )
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.blob_store = blobs
    app.state.import_jobs = ImportJobRunner(factory, blobs)
    if recover:
        recover_interrupted_jobs(factory)


def dispose_runtime(app: FastAPI) -> None:
    engine = getattr(app.state, "engine", None)
    if engine is not None:
        engine.dispose()
    app.state.engine = None


def switch_data_dir(
    app: FastAPI, dest: Path, *, smb_url: str | None = None, mount_point: str | None = None
) -> Settings:
    settings: Settings = app.state.settings
    dest = dest.expanduser().resolve()
    src = settings.data_dir.expanduser().resolve()
    boot = Path(settings.bootstrap_dir or settings.data_dir).expanduser().resolve()
    secret = getattr(app.state, "session_secret", "") or settings.session_secret
    copied: list[str] = []
    dispose_runtime(app)
    try:
        if dest != src:
            copied = copy_data_dir(
                src,
                dest,
                replace_payload=dest == boot,
                library_db=boot / "library.db",
            )
        new_settings = with_data_dir(settings, dest)
        attach_runtime(app, new_settings, recover=False)
        write_data_dir_config(
            boot, dest, smb_url=smb_url, mount_point=mount_point
        )
        if copied:
            _delete_named(src, copied)
        clear_bootstrap_library_payload(boot, dest)
    except Exception as exc:
        dispose_runtime(app)
        if dest != src:
            boot_db = boot / "library.db"
            if boot_db.is_file():
                try:
                    _rewrite_source_root_uris(boot_db, dest, src)
                except Exception:
                    pass
        if copied:
            _delete_named(dest, copied)
        attach_runtime(app, settings, recover=False)
        if secret:
            app.state.session_secret = secret
        if isinstance(exc, ValueError):
            raise
        raise ValueError(f"could not open the library in the new folder: {exc}") from exc
    if secret:
        app.state.session_secret = secret
    return new_settings
