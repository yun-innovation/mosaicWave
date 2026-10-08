from __future__ import annotations

import json
import shutil
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from mosaicwave.library.dump import DumpWarning, TakeoutInspect, inspect_takeout_path
from mosaicwave.library.takeout import import_takeout
from mosaicwave.library.takeoutpush import safe_staging_file, staging_dir, write_upload
from mosaicwave.library.thumbs import clear_placeholder_skips, ensure_thumb
from mosaicwave.storage.errors import InvalidPath
from mosaicwave.models import Album, AlbumAsset, Asset, Job, JobEvent, Source, new_id, utcnow
from mosaicwave.storage.blobstore import BlobStore

KIND_TAKEOUT = "takeout_import"
KIND_THUMB = "thumb_batch"
INTERRUPTED = "interrupted (process stopped)"


class ImportBusy(Exception):
    """Another job is already running."""


def _job_failure_message(current: str | None, exc: BaseException) -> str:
    text = str(exc)
    if current and current not in text:
        return f"{current}: {text}"
    return text


def _busy_detail(row: Job) -> str:
    inp = _parse_json(row.input_json)
    if row.kind == KIND_TAKEOUT and inp.get("mode") == "push" and inp.get("phase") == "receiving":
        return "A device upload is still running. Click Cancel upload, then Import again."
    return "Another job is already running"


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
        {"code": w.code, "severity": w.severity, "message": w.message, "path": w.path}
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
    def __init__(self, factory: sessionmaker[Session], blobs: BlobStore) -> None:
        self._factory = factory
        self._blobs = blobs
        self._lock = threading.RLock()
        self._running_id: str | None = None

    def _fail_if_busy(self, session: Session) -> None:
        if self._running_id is not None:
            current = session.get(Job, self._running_id)
            if current is not None and current.status == "running":
                raise ImportBusy(_busy_detail(current))
        busy = session.scalar(select(Job).where(Job.status == "running").limit(1))
        if busy is not None:
            raise ImportBusy(_busy_detail(busy))

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
                        raise ImportBusy(_busy_detail(current))
                busy = session.scalar(select(Job).where(Job.status == "running").limit(1))
                if busy is not None:
                    raise ImportBusy(_busy_detail(busy))
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
                raise ImportBusy("that import is not the active job")
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
                raise ImportBusy("that import is not the active job")
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
                raise ImportBusy("that import is not the active job")
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
                    raise ValueError(
                        "no photos found (need takeout-*.zip archives or an extracted Google Photos folder)"
                    )
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
                total = int(
                    session.scalar(
                        select(func.count()).select_from(Asset).where(
                            Asset.deleted_at.is_(None), Asset.source_id == source_id
                        )
                    )
                    or 0
                )
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

    def _run(self, job_id: str, root: Path, dest_root: Path, cleanup: Path | None = None) -> None:
        session = self._factory()
        try:
            row = session.get(Job, job_id)
            if row is None:
                return

            def on_progress(done: int, total: int, rel: str) -> None:
                row.processed = done
                row.total = total
                row.current = rel

            source = import_takeout(
                session,
                root,
                dest_root=dest_root,
                owner_user_id=row.user_id,
                on_progress=on_progress,
                commit_every=25,
            )
            assets, albums, memberships = _counts(session, source.id)
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
        except Exception as exc:
            try:
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
            session.close()
            if cleanup is not None:
                shutil.rmtree(cleanup, ignore_errors=True)
            with self._lock:
                if self._running_id == job_id:
                    self._running_id = None

    def _run_thumbs(self, job_id: str) -> None:
        session = self._factory()
        try:
            row = session.get(Job, job_id)
            if row is None:
                return
            inp = _parse_json(row.input_json)
            source_id = inp.get("source_id")
            stmt = select(Asset).where(Asset.deleted_at.is_(None)).order_by(Asset.id)
            if source_id:
                stmt = stmt.where(Asset.source_id == source_id)
            assets = list(session.scalars(stmt))
            row.total = len(assets)
            session.commit()
            clear_placeholder_skips()
            done = 0
            for asset in assets:
                source = session.get(Source, asset.source_id)
                if source is None or source.deleted_at is not None:
                    continue
                ensure_thumb(session, self._blobs, asset, source)
                done += 1
                row.processed = done
                row.current = asset.relative_path
                if done % 10 == 0:
                    session.commit()
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
        except Exception as exc:
            try:
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
            session.close()
            with self._lock:
                if self._running_id == job_id:
                    self._running_id = None
