# Job workflow and audit

**Phase 3 — done.** `job` / `job_event` in SQLite; Takeout import writes a job; `GET /api/v1/jobs`.

Related: [takeout.md](takeout.md), [schema.md](schema.md), [architecture.md](architecture.md).

## Why

## Why

Long tasks already exist (Takeout SHA-256 import). Today progress lives in **process memory**. Restart, `--reload`, or a crash loses the run. There is nothing to open later and answer “did import finish, what did it do, what failed?”

## Goal

1. Every long task is a **job** row in SQLite (`library.db`), owned by `job.user_id`.
2. Status and a short result survive process restart.
3. The caller can **list their past runs** (kind, times, status, error, counts).
4. Takeout import is the first `kind`; thumb batches use `thumb_batch`. Folder scan can share the table later.

## What it is not

- Temporal / Airflow / BPMN / user-defined graphs
- Distributed workers or a second process (v1: same uvicorn process, one job at a time)
- Client replica: jobs are **server audit**, not sync entities ([schema.md](schema.md) Phase 7)
- A replacement for `asset` / `album` rows — those stay the library

## Model (v1)

`job` (not syncable):

| Column | Role |
| --- | --- |
| `id` | UUID |
| `kind` | `takeout_import`, later `folder_scan`, `thumb_batch`, … |
| `status` | `queued` \| `running` \| `done` \| `error` \| `cancelled` |
| `created_at`, `started_at`, `finished_at` | UTC |
| `input_json` | Path and options (Takeout download folder, …) |
| `result_json` | Counts, `source_id`, warnings — when finished |
| `error` | Last error text (Takeout copy failures include the dump relative path; `current` stays on that file) |
| `processed`, `total`, `current` | Progress for the live UI |

Optional `job_event`: append-only (`job_id`, `at`, `level`, `message`) for a readable audit trail (started hashing, committed batch, sidecar gap, …). Keep it small; not a full log file.

## API

Keep Takeout routes as the **start** buttons; they create a job:

| Method | Path | Role |
| --- | --- | --- |
| POST | `/api/v1/import/takeout` | Create + start `takeout_import` from a **host path** (returns immediately) |
| POST | `/api/v1/import/takeout/push` | Create `takeout_import` (`mode=push`); client uploads files (`.../file?relative_path=`), then finish |
| GET | `/api/v1/jobs` | List recent jobs (**admin**); cursor, filter `kind` |
| GET | `/api/v1/jobs/{id}` | One job + progress / result (replaces ad-hoc import status) |
| GET | `/api/v1/jobs/{id}/events` | Audit events if `job_event` exists |
| POST | `/api/v1/jobs/{id}/cancel` | Stop a running job: its owner may, and so may an admin (any job). Others get `404 job_not_found`; an ended job gets `409 job_not_running`. See Cancelling |

## Cancelling

`POST /jobs/{id}/cancel` sets a flag the running job checks **between items**, so it ends as `cancelled` within moments (the reply still says `running`). An event "Cancel requested by owner|admin" is logged, and "Cancelled: …" when it stops. Asking twice is harmless. `GET /jobs/running` (admin) reports `cancelling`.

| Kind | What stays |
| --- | --- |
| `thumb_batch` | Thumbnails already made |
| `site_backup` | Nothing: the `.part` file is removed and no archive is written |
| `site_restore` | Nothing: the staging folders are removed and the library is unchanged |
| `library_backup` | Nothing: the `.part` file is removed and no archive is written |
| `library_restore` | Nothing while it is unpacking and checking: the staging folders are removed and the library is unchanged. Once the swap starts it is not cancellable (it takes moments, and rolls itself back on failure) |
| `takeout_import` | Every finished item (committed before the job ends); importing again skips what is there (same SHA-256) |
| `takeout_import` in `push` mode, still receiving | Cancelled at once by `/import/takeout/push/{id}/cancel` (ends as `error`, `cancelled`); `/jobs/{id}/cancel` hands over to it |

Inside the code a `JobCancelled` exception (a `BaseException`, so work that catches `Exception` cannot swallow it) unwinds the job; each runner catches it by name.

`GET /api/v1/import/takeout/status` may stay as an alias of the latest `takeout_import` job during Phase 3.

## UI

Admin: a short **Jobs** list (kind, status, started, result or error). Import progress on the library page reads the current job id. No timeline/gallery work here.

## Exit

- Restarting the API does not erase a finished or in-progress Takeout run (row remains; in-progress may be `error` if the process died).
- `GET /api/v1/jobs` returns that run.
- Pytest: start import on the fixture, list jobs, see `done` and counts.
