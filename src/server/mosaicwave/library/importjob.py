from __future__ import annotations

import json
import shutil
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from mosaicwave.backup import backup_site
from mosaicwave.config import Settings
from mosaicwave.library.dbs import LibraryDbs
from mosaicwave.restore import pending_exists, stage_site, write_pending
from mosaicwave.library.dump import DumpWarning, TakeoutInspect, inspect_takeout_path
from mosaicwave.library.takeout import import_takeout
from mosaicwave.library.takeoutpush import safe_staging_file, staging_dir, write_upload
from mosaicwave.library.thumbs import clear_placeholder_skips, ensure_thumb
from mosaicwave.storage.errors import InvalidPath
from mosaicwave.models import Album, AlbumAsset, Asset, Job, JobEvent, Source, new_id, utcnow
from mosaicwave.storage.blobstore import BlobStore

KIND_TAKEOUT = "takeout_import"
KIND_THUMB = "thumb_batch"
KIND_BACKUP = "site_backup"
KIND_RESTORE = "site_restore"
INTERRUPTED = "interrupted (process stopped)"


class JobCancelled(BaseException):
    """Raised inside a running job when someone asked it to stop.

    A BaseException so the ``except Exception`` blocks in the work being done (import, backup)
    cannot swallow it; each runner catches it by name and ends the job as ``cancelled``.
    """


class ImportBusy(Exception):
    """Another job is already running. ``code`` is the API error code."""

    def __init__(self, message: str, code: str = "job_running") -> None:
        super().__init__(message)
        self.code = code


class NoPhotosFound(ValueError):
    def __init__(self) -> None:
        super().__init__(
            "no photos found (need takeout-*.zip archives or an extracted Google Photos folder)"
        )


def _job_failure_message(current: str | None, exc: BaseException) -> str:
    text = str(exc)
    if current and current not in text:
        return f"{current}: {text}"
    return text


def _busy(row: Job) -> ImportBusy:
    inp = _parse_json(row.input_json)
    if row.kind == KIND_TAKEOUT and inp.get("mode") == "push" and inp.get("phase") == "receiving":
        return ImportBusy(
            "A device upload is still running. Click Cancel upload, then Import again.",
            "upload_still_running",
        )
    return ImportBusy("Another job is already running")


def _counts(session: Session, source_id: str) -> tuple[int, int, int]:
    assets = int(
        session.scalar(
            select(func.count()).select_from(Asset).where(
                Asset.source_id == source_id, Asset.deleted_at.is_(None)
            )
        )
        or 0
    )
    albums = int(
        session.scalar(
            select(func.count()).select_from(Album).where(
                Album.source_id == source_id, Album.deleted_at.is_(None)
            )
        )
        or 0
    )
    memberships = int(
        session.scalar(
            select(func.count()).select_from(AlbumAsset).where(
                AlbumAsset.deleted_at.is_(None),
                AlbumAsset.album_id.in_(
                    select(Album.id).where(Album.source_id == source_id)
                ),
            )
        )
        or 0
    )
    return assets, albums, memberships


def _parse_json(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _warnings_from_report(report: TakeoutInspect) -> list[dict]:
    return [
        {
            "code": w.code,
            "severity": w.severity,
            "message": w.message,
            "path": w.path,
            "params": dict(w.params),
        }
        for w in report.warnings
    ]


def _warnings_from_result(data: dict) -> list[DumpWarning]:
    out: list[DumpWarning] = []
    raw = data.get("warnings")
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append(
            DumpWarning(
                code=str(item.get("code") or ""),
                severity=str(item.get("severity") or "warning"),
                message=str(item.get("message") or ""),
                path=item.get("path") if isinstance(item.get("path"), str) else None,
                params=item.get("params") if isinstance(item.get("params"), dict) else {},
            )
        )
    return out


def job_view(row: Job) -> dict:
    inp = _parse_json(row.input_json)
    result = _parse_json(row.result_json)
    return {
        "id": row.id,
        "kind": row.kind,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "path": inp.get("path"),
        "processed": row.processed,
        "total": row.total,
        "current": row.current,
        "source_id": result.get("source_id"),
        "assets": int(result.get("assets") or 0),
        "albums": int(result.get("albums") or 0),
        "memberships": int(result.get("memberships") or 0),
        "import_root": result.get("import_root") or inp.get("import_root"),
        "mode": inp.get("mode") or "host",
        "phase": inp.get("phase"),
        "error": row.error,
        "warnings": _warnings_from_result(result),
    }


def idle_view() -> dict:
    return {
        "id": None,
        "kind": None,
        "status": "idle",
        "created_at": None,
        "started_at": None,
        "finished_at": None,
        "path": None,
        "processed": 0,
        "total": 0,
        "current": None,
        "source_id": None,
        "assets": 0,
        "albums": 0,
        "memberships": 0,
        "import_root": None,
        "mode": None,
        "phase": None,
        "error": None,
        "warnings": [],
    }


def recover_interrupted_jobs(factory: sessionmaker[Session]) -> None:
    session = factory()
    try:
        rows = list(session.scalars(select(Job).where(Job.status == "running")))
        if not rows:
            return
        now = utcnow()
        for row in rows:
            staging = _parse_json(row.input_json).get("staging")
            if isinstance(staging, str) and staging:
                shutil.rmtree(staging, ignore_errors=True)
            row.status = "error"
            row.error = INTERRUPTED
            row.finished_at = now
            row.current = None
            session.add(
                JobEvent(
                    id=new_id(),
                    job_id=row.id,
                    level="error",
                    message=INTERRUPTED,
                )
            )
        session.commit()
    finally:
        session.close()


def list_jobs(session: Session, *, kind: str | None, limit: int, user_id: str | None = None) -> list[Job]:
    limit = max(1, min(limit, 100))
    stmt = select(Job).order_by(Job.created_at.desc()).limit(limit)
    if kind:
        stmt = stmt.where(Job.kind == kind)
    if user_id:
        stmt = stmt.where(Job.user_id == user_id)
    return list(session.scalars(stmt))


def list_job_events(session: Session, job_id: str) -> list[JobEvent]:
    return list(
        session.scalars(
            select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.at.asc())
        )
    )


class ImportJobRunner:
    def __init__(
        self,
        factory: sessionmaker[Session],
        blobs: BlobStore,
        libraries: LibraryDbs,
        settings: Settings | None = None,
    ) -> None:
        # `factory` is the site database (jobs). Photos and albums live in the owner's own
        # library database, opened through `libraries`. `settings` is only needed for backups.
        self._factory = factory
        self._libraries = libraries
        self._settings = settings
        self._blobs = blobs
        self._lock = threading.RLock()
        self._running_id: str | None = None
        # job id -> set when someone asked it to stop; the job checks between items.
        self._cancels: dict[str, threading.Event] = {}

    def is_cancelling(self, job_id: str) -> bool:
        event = self._cancels.get(job_id)
        return bool(event and event.is_set())

    def _check_cancel(self, job_id: str) -> None:
        if self.is_cancelling(job_id):
            raise JobCancelled()

    def cancel(self, job_id: str, *, by: str) -> dict:
        """Ask a running job to stop. It ends as ``cancelled`` at its next check, within moments.

        ``by`` is ``owner`` or ``admin`` (for the job log). Asking twice is harmless. A device
        upload that is still receiving files is cancelled at once, as before.
        """
        with self._lock:
            session = self._factory()
            try:
                row = session.get(Job, job_id)
                if row is None:
                    raise ImportBusy("job not found", "job_not_found")
                if row.status != "running":
                    raise ImportBusy("This job is not running", "job_not_running")
                inp = _parse_json(row.input_json)
                if row.kind == KIND_TAKEOUT and inp.get("mode") == "push" and inp.get("phase") == "receiving":
                    owner = row.user_id or ""
                else:
                    owner = None
                if owner is None:
                    event = self._cancels.setdefault(job_id, threading.Event())
                    if not event.is_set():
                        event.set()
                        session.add(
                            JobEvent(
                                id=new_id(), job_id=row.id, level="info", message=f"Cancel requested by {by}"
                            )
                        )
                        session.commit()
                    return job_view(row)
            finally:
                session.close()
            return self.cancel_push(job_id, user_id=owner)

    def _mark_cancelled(self, session: Session, job_id: str, note: str) -> None:
        row = session.get(Job, job_id)
        if row is not None:
            row.status = "cancelled"
            row.error = None
            row.finished_at = utcnow()
            row.current = None
            session.add(JobEvent(id=new_id(), job_id=row.id, level="info", message=f"Cancelled: {note}"))
        session.commit()

    def _fail_if_busy(self, session: Session) -> None:
        if self._settings is not None and pending_exists(self._settings):
            raise ImportBusy(
                "A restored copy is waiting to be applied. Apply or discard it first.", "restore_pending"
            )
        if self._running_id is not None:
            current = session.get(Job, self._running_id)
            if current is not None and current.status == "running":
                raise _busy(current)
        busy = session.scalar(select(Job).where(Job.status == "running").limit(1))
        if busy is not None:
            raise _busy(busy)

    @contextmanager
    def idle_exclusive(self, session: Session):
        with self._lock:
            self._fail_if_busy(session)
            yield

    def latest_takeout(self, user_id: str | None = None) -> dict:
        session = self._factory()
        try:
            stmt = select(Job).where(Job.kind == KIND_TAKEOUT).order_by(Job.created_at.desc())
            if user_id:
                stmt = stmt.where(Job.user_id == user_id)
            row = session.scalar(stmt.limit(1))
            if row is None:
                return idle_view()
            return job_view(row)
        finally:
            session.close()

    def start(self, root: Path, report: TakeoutInspect, dest_root: Path, *, user_id: str | None = None) -> dict:
        root = root.resolve()
        dest_root = dest_root.expanduser().resolve()
        path = str(root)
        with self._lock:
            session = self._factory()
            try:
                if self._running_id is not None:
                    current = session.get(Job, self._running_id)
                    if current is not None and current.status == "running":
                        inp = _parse_json(current.input_json)
                        if inp.get("path") == path:
                            return job_view(current)
                        raise _busy(current)
                busy = session.scalar(select(Job).where(Job.status == "running").limit(1))
                if busy is not None:
                    raise _busy(busy)
                now = utcnow()
                warnings = _warnings_from_report(report)
                row = Job(
                    id=new_id(),
                    kind=KIND_TAKEOUT,
                    status="running",
                    created_at=now,
                    started_at=now,
                    user_id=user_id,
                    input_json=json.dumps(
                        {"path": path, "dest": str(dest_root), "import_root": report.import_root},
                        sort_keys=True,
                    ),
                    result_json=json.dumps(
                        {"import_root": report.import_root, "warnings": warnings},
                        sort_keys=True,
                    ),
                )
                session.add(row)
                session.add(
                    JobEvent(
                        id=new_id(),
                        job_id=row.id,
                        level="info",
                        message=f"Takeout import started: {path} → {dest_root}",
                    )
                )
                session.commit()
                job_id = row.id
                view = job_view(row)
            finally:
                session.close()
            self._running_id = job_id
            thread = threading.Thread(
                target=self._run, args=(job_id, root, dest_root, None), daemon=True
            )
            thread.start()
            return view

    def start_push(self, *, user_id: str, data_dir: Path, file_count: int | None = None) -> dict:
        job_id = new_id()
        staging = staging_dir(data_dir, job_id)
        with self._lock:
            session = self._factory()
            try:
                self._fail_if_busy(session)
                staging.mkdir(parents=True, exist_ok=True)
                now = utcnow()
                row = Job(
                    id=job_id,
                    kind=KIND_TAKEOUT,
                    status="running",
                    created_at=now,
                    started_at=now,
                    user_id=user_id,
                    processed=0,
                    total=int(file_count or 0),
                    current="waiting for files",
                    input_json=json.dumps(
                        {
                            "mode": "push",
                            "phase": "receiving",
                            "staging": str(staging),
                        },
                        sort_keys=True,
                    ),
                    result_json=json.dumps({"import_root": None, "warnings": []}, sort_keys=True),
                )
                session.add(row)
                session.add(
                    JobEvent(
                        id=new_id(),
                        job_id=row.id,
                        level="info",
                        message="Takeout push started (client upload)",
                    )
                )
                session.commit()
                job_id = row.id
                view = job_view(row)
            finally:
                session.close()
            self._running_id = job_id
            return view

    def _push_job(self, session: Session, job_id: str, user_id: str) -> Job:
        row = session.get(Job, job_id)
        if row is None or row.kind != KIND_TAKEOUT or row.user_id != user_id:
            raise FileNotFoundError("job not found")
        if row.status != "running":
            raise ValueError("that import is not receiving files")
        inp = _parse_json(row.input_json)
        if inp.get("mode") != "push":
            raise ValueError("that import is not a client upload")
        return row

    def prepare_push_file(
        self, job_id: str, *, user_id: str, relative_path: str
    ) -> tuple[Path, bool]:
        with self._lock:
            if self._running_id != job_id:
                raise ImportBusy("that import is not the active job", "job_not_active")
        session = self._factory()
        try:
            row = self._push_job(session, job_id, user_id)
            inp = _parse_json(row.input_json)
            if inp.get("phase") != "receiving":
                raise ValueError("upload finished; cannot add more files")
            staging = Path(str(inp.get("staging") or ""))
            if not staging:
                raise ValueError("missing staging folder")
            try:
                dest = safe_staging_file(staging, relative_path)
            except (InvalidPath, ValueError) as exc:
                raise ValueError(str(exc)) from exc
            return dest, dest.is_file()
        finally:
            session.close()

    def commit_push_file(
        self, job_id: str, *, user_id: str, relative_path: str, existed: bool
    ) -> dict:
        with self._lock:
            if self._running_id != job_id:
                raise ImportBusy("that import is not the active job", "job_not_active")
        session = self._factory()
        try:
            row = self._push_job(session, job_id, user_id)
            inp = _parse_json(row.input_json)
            if inp.get("phase") != "receiving":
                raise ValueError("upload finished; cannot add more files")
            row.current = relative_path.replace("\\", "/")
            if not existed:
                row.processed = int(row.processed) + 1
            session.commit()
            return job_view(row)
        finally:
            session.close()

    def receive_file(
        self, job_id: str, *, user_id: str, relative_path: str, stream: BinaryIO
    ) -> dict:
        dest, existed = self.prepare_push_file(
            job_id, user_id=user_id, relative_path=relative_path
        )
        try:
            write_upload(dest, stream)
        except OSError as exc:
            raise ValueError(f"could not save {relative_path}: {exc}") from exc
        return self.commit_push_file(
            job_id, user_id=user_id, relative_path=relative_path, existed=existed
        )

    def finish_push(self, job_id: str, *, user_id: str, dest_root: Path) -> dict:
        dest_root = dest_root.expanduser().resolve()
        with self._lock:
            if self._running_id != job_id:
                raise ImportBusy("that import is not the active job", "job_not_active")
            session = self._factory()
            try:
                row = self._push_job(session, job_id, user_id)
                inp = _parse_json(row.input_json)
                if inp.get("phase") != "receiving":
                    return job_view(row)
                staging = Path(str(inp.get("staging") or "")).resolve()
                if not staging.is_dir():
                    raise ValueError("missing staging folder")
                report = inspect_takeout_path(staging)
                if not report.import_root:
                    raise NoPhotosFound()
                inp["phase"] = "importing"
                inp["path"] = str(staging)
                inp["import_root"] = report.import_root
                row.input_json = json.dumps(inp, sort_keys=True)
                result = _parse_json(row.result_json)
                result["import_root"] = report.import_root
                result["warnings"] = _warnings_from_report(report)
                row.result_json = json.dumps(result, sort_keys=True)
                row.current = "importing"
                session.add(
                    JobEvent(
                        id=new_id(),
                        job_id=row.id,
                        level="info",
                        message=f"Takeout push received; importing {staging}",
                    )
                )
                session.commit()
                view = job_view(row)
            finally:
                session.close()
            thread = threading.Thread(
                target=self._run, args=(job_id, staging, dest_root, staging), daemon=True
            )
            thread.start()
            return view

    def cancel_push(self, job_id: str, *, user_id: str) -> dict:
        with self._lock:
            session = self._factory()
            staging: str | None = None
            try:
                row = self._push_job(session, job_id, user_id)
                inp = _parse_json(row.input_json)
                raw_staging = inp.get("staging")
                staging = raw_staging if isinstance(raw_staging, str) else None
                row.status = "error"
                row.error = "cancelled"
                row.finished_at = utcnow()
                row.current = None
                session.add(
                    JobEvent(
                        id=new_id(),
                        job_id=row.id,
                        level="info",
                        message="Takeout push cancelled",
                    )
                )
                session.commit()
                view = job_view(row)
            finally:
                session.close()
            if self._running_id == job_id:
                self._running_id = None
            if isinstance(staging, str) and staging:
                shutil.rmtree(staging, ignore_errors=True)
            return view

    def start_thumbs(self, *, user_id: str, source_id: str) -> dict:
        with self._lock:
            session = self._factory()
            try:
                self._fail_if_busy(session)
                lib = self._libraries.factory(user_id)()
                try:
                    total = int(
                        lib.scalar(
                            select(func.count()).select_from(Asset).where(
                                Asset.deleted_at.is_(None), Asset.source_id == source_id
                            )
                        )
                        or 0
                    )
                finally:
                    lib.close()
                now = utcnow()
                row = Job(
                    id=new_id(),
                    kind=KIND_THUMB,
                    status="running",
                    created_at=now,
                    started_at=now,
                    user_id=user_id,
                    input_json=json.dumps({"source_id": source_id}, sort_keys=True),
                    result_json="{}",
                    total=total,
                )
                session.add(row)
                session.add(
                    JobEvent(
                        id=new_id(),
                        job_id=row.id,
                        level="info",
                        message=f"Thumb batch started ({total} assets)",
                    )
                )
                session.commit()
                job_id = row.id
                view = job_view(row)
            finally:
                session.close()
            self._running_id = job_id
        thread = threading.Thread(target=self._run_thumbs, args=(job_id,), daemon=True)
        thread.start()
        return view

    def start_backup(self, *, user_id: str, include_thumbs: bool) -> dict:
        if self._settings is None:
            raise RuntimeError("backup needs the app settings")
        with self._lock:
            session = self._factory()
            try:
                self._fail_if_busy(session)
                now = utcnow()
                row = Job(
                    id=new_id(),
                    kind=KIND_BACKUP,
                    status="running",
                    created_at=now,
                    started_at=now,
                    user_id=user_id,
                    input_json=json.dumps({"include_thumbs": include_thumbs}, sort_keys=True),
                    result_json="{}",
                )
                session.add(row)
                session.add(
                    JobEvent(id=new_id(), job_id=row.id, level="info", message="Site backup started")
                )
                session.commit()
                job_id = row.id
                view = job_view(row)
            finally:
                session.close()
            self._running_id = job_id
        thread = threading.Thread(target=self._run_backup, args=(job_id, include_thumbs), daemon=True)
        thread.start()
        return view

    def _run_backup(self, job_id: str, include_thumbs: bool) -> None:
        assert self._settings is not None
        session = self._factory()
        try:
            row = session.get(Job, job_id)
            if row is None:
                return

            def on_progress(done: int, total: int, rel: str) -> None:
                row.processed = done
                row.total = total
                row.current = rel
                if done % 25 == 0:
                    session.commit()
                self._check_cancel(job_id)

            result = backup_site(
                self._settings,
                self._settings.backup_dir,
                include_thumbs=include_thumbs,
                on_progress=on_progress,
            )
            manifest = result.manifest
            row.status = "done"
            row.finished_at = utcnow()
            row.current = None
            row.processed = row.total
            row.error = None
            row.result_json = json.dumps(
                {"backup": result.path.name, "assets": manifest["assets"], "albums": manifest["albums"]},
                sort_keys=True,
            )
            session.add(
                JobEvent(
                    id=new_id(),
                    job_id=row.id,
                    level="info",
                    message=f"Backup {result.path.name}: {manifest['assets']} assets, {manifest['file_count']} files",
                )
            )
            for warning in manifest["warnings"]:
                session.add(JobEvent(id=new_id(), job_id=row.id, level="info", message=warning))
            session.commit()
        except JobCancelled:
            try:
                self._mark_cancelled(session, job_id, "no archive was written")
            except Exception:
                session.rollback()
        except Exception as exc:  # ArchiveError, OSError, sqlite errors: all end the job the same way
            try:
                session.rollback()
                row = session.get(Job, job_id)
                if row is not None:
                    row.status = "error"
                    row.error = str(exc)
                    row.finished_at = utcnow()
                    row.current = None
                    session.add(JobEvent(id=new_id(), job_id=row.id, level="error", message=str(exc)))
                session.commit()
            except Exception:
                session.rollback()
        finally:
            session.close()
            with self._lock:
                self._cancels.pop(job_id, None)
                if self._running_id == job_id:
                    self._running_id = None

    def start_restore(self, *, user_id: str, archive: Path) -> dict:
        """Unpack and check a site backup beside the live data (nothing live changes).

        When it is done the restore is *staged*; the admin applies it, which restarts the server.
        """
        if self._settings is None:
            raise RuntimeError("restore needs the app settings")
        with self._lock:
            session = self._factory()
            try:
                self._fail_if_busy(session)
                now = utcnow()
                row = Job(
                    id=new_id(),
                    kind=KIND_RESTORE,
                    status="running",
                    created_at=now,
                    started_at=now,
                    user_id=user_id,
                    input_json=json.dumps({"archive": archive.name}, sort_keys=True),
                    result_json="{}",
                )
                session.add(row)
                session.add(
                    JobEvent(
                        id=new_id(), job_id=row.id, level="info", message=f"Restore of {archive.name} started"
                    )
                )
                session.commit()
                job_id = row.id
                view = job_view(row)
            finally:
                session.close()
            self._running_id = job_id
        thread = threading.Thread(target=self._run_restore, args=(job_id, archive), daemon=True)
        thread.start()
        return view

    def _run_restore(self, job_id: str, archive: Path) -> None:
        assert self._settings is not None
        session = self._factory()
        try:
            row = session.get(Job, job_id)
            if row is None:
                return

            def on_progress(done: int, total: int, _rel: str) -> None:
                # No file names here: they are photo paths, and this job is visible to admins only as a bar.
                row.processed = done
                row.total = total
                if done % 25 == 0:
                    session.commit()
                self._check_cancel(job_id)

            staged = stage_site(archive, self._settings, on_progress=on_progress)
            try:
                write_pending(self._settings, staged, archive.name)
            except BaseException:
                shutil.rmtree(staged.app_stage, ignore_errors=True)
                shutil.rmtree(staged.data_stage, ignore_errors=True)
                raise
            manifest = staged.manifest
            row.status = "done"
            row.finished_at = utcnow()
            row.current = None
            row.processed = row.total
            row.error = None
            row.result_json = json.dumps(
                {"archive": archive.name, "assets": manifest["assets"], "albums": manifest["albums"]},
                sort_keys=True,
            )
            session.add(
                JobEvent(
                    id=new_id(),
                    job_id=row.id,
                    level="info",
                    message=(
                        f"Restore of {archive.name} is staged: {manifest['assets']} assets, "
                        f"{manifest['users']} users. Apply it to swap it in."
                    ),
                )
            )
            session.commit()
        except JobCancelled:
            try:
                self._mark_cancelled(session, job_id, "nothing was staged; the library is unchanged")
            except Exception:
                session.rollback()
        except Exception as exc:  # ArchiveError, OSError, sqlite errors: all end the job the same way
            try:
                session.rollback()
                row = session.get(Job, job_id)
                if row is not None:
                    row.status = "error"
                    row.error = str(exc)
                    row.finished_at = utcnow()
                    row.current = None
                    session.add(JobEvent(id=new_id(), job_id=row.id, level="error", message=str(exc)))
                session.commit()
            except Exception:
                session.rollback()
        finally:
            session.close()
            with self._lock:
                self._cancels.pop(job_id, None)
                if self._running_id == job_id:
                    self._running_id = None

    def _run(self, job_id: str, root: Path, dest_root: Path, cleanup: Path | None = None) -> None:
        session = self._factory()
        lib: Session | None = None
        try:
            row = session.get(Job, job_id)
            if row is None:
                return
            if not row.user_id:
                raise ValueError("import job has no owner")
            lib = self._libraries.factory(row.user_id)()

            def on_progress(done: int, total: int, rel: str) -> None:
                row.processed = done
                row.total = total
                row.current = rel
                if done % 25 == 0:
                    session.commit()
                self._check_cancel(job_id)

            source = import_takeout(
                lib,
                root,
                dest_root=dest_root,
                owner_user_id=row.user_id,
                on_progress=on_progress,
                commit_every=25,
            )
            lib.commit()
            assets, albums, memberships = _counts(lib, source.id)
            result = _parse_json(row.result_json)
            result.update(
                {
                    "source_id": source.id,
                    "assets": assets,
                    "albums": albums,
                    "memberships": memberships,
                }
            )
            row.status = "done"
            row.finished_at = utcnow()
            row.current = None
            row.processed = row.total
            row.error = None
            row.result_json = json.dumps(result, sort_keys=True)
            session.add(
                JobEvent(
                    id=new_id(),
                    job_id=row.id,
                    level="info",
                    message=f"Imported {assets} assets, {albums} albums",
                )
            )
            session.commit()
        except JobCancelled:
            # The check runs between items, so everything done so far is whole: keep it. A new
            # import skips what is already there (same SHA-256).
            try:
                if lib is not None:
                    lib.commit()
                self._mark_cancelled(session, job_id, "the items imported so far are kept")
            except Exception:
                session.rollback()
        except Exception as exc:
            try:
                if lib is not None:
                    lib.rollback()
                row = session.get(Job, job_id)
                if row is not None:
                    row.status = "error"
                    row.error = _job_failure_message(row.current, exc)
                    row.finished_at = utcnow()
                    session.add(
                        JobEvent(
                            id=new_id(),
                            job_id=row.id,
                            level="error",
                            message=row.error,
                        )
                    )
                session.commit()
            except Exception:
                session.rollback()
        finally:
            if lib is not None:
                lib.close()
            session.close()
            if cleanup is not None:
                shutil.rmtree(cleanup, ignore_errors=True)
            with self._lock:
                self._cancels.pop(job_id, None)
                if self._running_id == job_id:
                    self._running_id = None

    def _run_thumbs(self, job_id: str) -> None:
        session = self._factory()
        lib: Session | None = None
        try:
            row = session.get(Job, job_id)
            if row is None:
                return
            if not row.user_id:
                raise ValueError("thumbnail job has no owner")
            lib = self._libraries.factory(row.user_id)()
            inp = _parse_json(row.input_json)
            source_id = inp.get("source_id")
            stmt = select(Asset).where(Asset.deleted_at.is_(None)).order_by(Asset.id)
            if source_id:
                stmt = stmt.where(Asset.source_id == source_id)
            assets = list(lib.scalars(stmt))
            row.total = len(assets)
            session.commit()
            clear_placeholder_skips()
            done = 0
            for asset in assets:
                self._check_cancel(job_id)
                source = lib.get(Source, asset.source_id)
                if source is None or source.deleted_at is not None:
                    continue
                ensure_thumb(lib, self._blobs, asset, source)
                done += 1
                row.processed = done
                row.current = asset.relative_path
                if done % 10 == 0:
                    lib.commit()
                    session.commit()
            lib.commit()
            row.status = "done"
            row.finished_at = utcnow()
            row.current = None
            row.processed = done
            row.error = None
            row.result_json = json.dumps({"thumbs": done}, sort_keys=True)
            session.add(
                JobEvent(
                    id=new_id(),
                    job_id=row.id,
                    level="info",
                    message=f"Generated {done} thumbnails",
                )
            )
            session.commit()
        except JobCancelled:
            try:
                if lib is not None:
                    lib.commit()
                self._mark_cancelled(session, job_id, "the thumbnails made so far are kept")
            except Exception:
                session.rollback()
        except Exception as exc:
            try:
                if lib is not None:
                    lib.rollback()
                row = session.get(Job, job_id)
                if row is not None:
                    row.status = "error"
                    row.error = str(exc)
                    row.finished_at = utcnow()
                    row.current = None
                    session.add(
                        JobEvent(
                            id=new_id(),
                            job_id=row.id,
                            level="error",
                            message=str(exc),
                        )
                    )
                session.commit()
            except Exception:
                session.rollback()
        finally:
            if lib is not None:
                lib.close()
            session.close()
            with self._lock:
                self._cancels.pop(job_id, None)
                if self._running_id == job_id:
                    self._running_id = None
