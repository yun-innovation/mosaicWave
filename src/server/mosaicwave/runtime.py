from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy.orm import sessionmaker

from mosaicwave.config import (
    Settings,
    _delete_named,
    _rewrite_library_root_uris,
    clear_bootstrap_library_payload,
    copy_data_dir,
    session_secret_for,
    with_data_dir,
    write_data_dir_config,
)
from mosaicwave.datamove import clear_journal, write_journal
from mosaicwave.db import make_engine
from mosaicwave.library.dbs import LibraryDbs
from mosaicwave.library.importjob import ImportJobRunner, recover_interrupted_jobs
from mosaicwave.storage.blobstore import LocalBlobStore


_log = logging.getLogger(__name__)


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
    app.state.library_dbs = LibraryDbs(settings)
    app.state.import_jobs = ImportJobRunner(factory, blobs, app.state.library_dbs, settings)
    if recover:
        recover_interrupted_jobs(factory)


def dispose_runtime(app: FastAPI) -> None:
    library_dbs = getattr(app.state, "library_dbs", None)
    if library_dbs is not None:
        library_dbs.dispose_all()
    engine = getattr(app.state, "engine", None)
    if engine is not None:
        engine.dispose()
    app.state.engine = None


def _probe_new_runtime(app: FastAPI, settings: Settings, names: list[str]) -> None:
    """The new folder must answer before the old files are deleted."""
    with app.state.engine.connect() as conn:
        conn.exec_driver_sql("SELECT 1")
    for name in names:
        if not (settings.data_dir / name).exists():
            raise ValueError(f"{name} is missing from the new folder")


def switch_data_dir(
    app: FastAPI,
    dest: Path,
    *,
    smb_url: str | None = None,
    mount_point: str | None = None,
    on_progress: Callable[[int, int, int, int], None] | None = None,
    check: Callable[[], None] | None = None,
) -> Settings:
    """Move the data folder (milestone 13, doc/maintenance.md).

    The copy runs with the library still attached (the caller keeps requests away with
    ``app.state.moving``); it is journaled, verified, and only then are the databases detached for
    the few seconds the source paths are rewritten. The old files are deleted last, after the new
    folder answers and ``config.json`` points at it. ``check`` raises to cancel the copy.
    """
    settings: Settings = app.state.settings
    dest = dest.expanduser().resolve()
    src = settings.data_dir.expanduser().resolve()
    boot = Path(settings.bootstrap_dir or settings.data_dir).expanduser().resolve()
    secret = getattr(app.state, "session_secret", "") or settings.session_secret
    copied: list[str] = []
    detached = False

    def detach() -> None:
        nonlocal detached
        dispose_runtime(app)
        detached = True

    def stage(phase: str, names: list[str]) -> None:
        write_journal(settings, src, dest, names, phase)

    try:
        if dest != src:
            copied = copy_data_dir(
                src,
                dest,
                replace_payload=dest == boot,
                library_db=boot / "library.db",
                on_progress=on_progress,
                check=check,
                on_stage=stage,
                before_rewrite=detach,
            )
        if not detached:
            detach()  # same folder (or nothing to copy): still re-attach with the new settings
        new_settings = with_data_dir(settings, dest)
        attach_runtime(app, new_settings, recover=False)
        _probe_new_runtime(app, new_settings, copied)
        write_data_dir_config(boot, dest, smb_url=smb_url, mount_point=mount_point)
    except BaseException as exc:
        cleaned = True
        try:
            if detached:
                dispose_runtime(app)
                if dest != src:
                    boot_db = boot / "library.db"
                    if boot_db.is_file():
                        try:
                            _rewrite_library_root_uris(boot_db, dest, src)
                        except Exception:
                            cleaned = False
            if copied:
                try:
                    _delete_named(dest, copied)
                except Exception:
                    cleaned = False
        finally:
            # Whatever the cleanup did, the old library must come back, or the
            # server keeps answering 500 with no engine.
            if detached:
                attach_runtime(app, settings, recover=False)
        if secret:
            app.state.session_secret = secret
        if cleaned:
            clear_journal(settings)
        if not isinstance(exc, Exception) or isinstance(exc, ValueError):
            raise
        raise ValueError(f"could not open the library in the new folder: {exc}") from exc
    # config.json now points at the new folder, so it is live. What is left is cleanup: if it fails the
    # journal stays and the next start finishes it (recover_data_dir_move).
    try:
        if copied:
            _delete_named(src, copied)
        clear_bootstrap_library_payload(boot, dest)
        clear_journal(settings)
    except OSError:
        _log.exception("the old library files could not all be deleted; they are removed at next start")
    if secret:
        app.state.session_secret = secret
    return new_settings