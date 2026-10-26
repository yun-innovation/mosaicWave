# Maintenance mode

Milestone 12. An admin locks everyone else out of the server so a backup or restore can run while nobody changes the data. The online site restore ([backup.md](backup.md), milestone 7 PR 2) builds on it.

## What it does

- **Flag file:** `maintenance.json` beside the site database (the app folder; with `MOSAICWAVE_DATA_DIR` set, the data folder). `backups/` and `exports/` live in the data folder. It holds `reason`, `since` and `by` (a user id, `cli` or `env`). It survives a restart and is never part of an archive.
- **State is derived, not stored:**

| State | Meaning |
| --- | --- |
| `off` | No flag file |
| `draining` | Flag file and a job is running. The job finishes; every new job is refused with `job_running` because one is already running |
| `on` | Flag file and no running job |

- **Guard:** while the flag exists, only an admin may call `/api/v1/*`. Everyone else, signed in or not, gets `503` with code `maintenance`, `params` `{reason, since}` and a `Retry-After: 10` header. Open to everybody: `GET /health`, `/maintenance`, `/auth/status`, `/auth/login`, `/auth/logout`, and the static web files.
- **Sign-in:** `POST /auth/login` checks the password first. A non-admin who is right then gets `503 maintenance` (a wrong password is still `401`), so nobody learns more than they could from `/maintenance`. Sessions that already exist stay valid and work again when maintenance ends.
- **The admin's own library:** an admin can still use it through the API; the web UI hides it. The online restore does not lock it: it stages first and swaps at restart, and anything the admin changes in between is lost (the Backup page says so). A stricter lock is not planned.

## Switching it

| How | Command |
| --- | --- |
| Web (admin) | **Maintenance** page (`#/maintenance`): optional message, then **Turn on** (asks once) or **Turn off** |
| API | `PUT /api/v1/maintenance` `{enabled, reason}` (admin) |
| Command line | `python -m mosaicwave.maintenance on [--reason TEXT]`, `off`, `status`. Works with the API running or stopped, because the API reads the file on every request |
| Start already locked | Set `MOSAICWAVE_MAINTENANCE=1` before starting the API. It writes the flag with the reason "Started in maintenance mode" unless one exists already. Turn it off from the web or the command line |

Switching it on twice replaces the message and keeps `since`. The message is plain text, up to 500 characters, shown as typed (not translated). A flag file that cannot be read still counts as maintenance (fail closed).

## API

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/v1/maintenance` | **Public.** `{state: off\|draining\|on, reason, since}` |
| PUT | `/api/v1/maintenance` | **Admin.** `{enabled: bool, reason: string}`; returns the new status |
| GET | `/api/v1/jobs/running` | **Admin.** `{job: null}` or the one job running on the server, whoever started it: `id`, `kind`, `status`, `started_at`, `processed`, `total`, `owner` (display name), `cancelling`. It leaves out `current`, `path`, warnings and the event log, because those name a user's files. `/jobs`, `/jobs/{id}` and `/jobs/{id}/events` stay owner-only |

## Web

- `useMaintenance` polls the public status every 15 s, every 3 s while it is not `off`, and at once when any call is refused with `503 maintenance` (`apiFetch` raises an event). `MaintenanceStatus` is in `types.ts`.
- **Everyone but an admin** (signed out or a member) gets `MaintenanceScreen`: the message, since when, "checking again automatically", and **Sign out** for a member or **Administrator sign in** when signed out. The app returns by itself when maintenance ends.
- **While `draining`** an admin's page polls `GET /jobs/running` every 2 s (`useRunningJob`). The Maintenance page shows "Waiting for", the job type and owner, `processed / total` with a progress bar and the start time; the banner shows the same in one line. An admin can **Cancel job** there (after one confirmation that says what stays); the block then shows "Cancelling…" and the state moves to `on` when the job has stopped ([workflow.md § Cancelling](workflow.md#cancelling)).
- **An admin** sees a banner with the message and a **Manage** link, the upload button and Settings link are hidden, and the sidebar shrinks to Server, Backup, Maintenance and Activity (Server because Move library needs maintenance mode). Any other URL shows Backup.
- Strings: namespace `maintenance`, `nav.maintenance` in `common`, and `errors:maintenance`.

## Move library under maintenance (milestone 13)

**Move library** (Server page, `PUT /api/v1/settings`) used to run in one long request while users kept working: the library was detached for the whole copy, requests in between failed, a crash mid-copy could leave the files split, and a multi-hundred-GB copy could outlast the HTTP request. It is now protected in five ways.

1. **Maintenance gate.** The move is refused unless maintenance is `on` (`409 maintenance_required`; while `draining`, a staged restore or a running job it is `409 data_dir_job_running`; `409 data_dir_env_locked` when `MOSAICWAVE_DATA_DIR` is set). This covers saving the same folder too. The Server page is available during maintenance (it is in the admin's short menu), disables the form until the state is `on`, and links to the Maintenance page. The checks that need no copying run first and answer `400 data_dir_rejected`: absolute path, writable, not inside the current folder (or the reverse), enough free space; `409 data_dir_occupied` when the destination holds another library.
2. **A job, not a request.** `PUT /settings` starts a cancellable job of kind `data_dir_move` and returns it (`ImportOut`, the same shape as every job). The page and the status bar follow it with `GET /jobs/{id}`; progress is `processed / total` in MiB, never a file name, and the result carries `restarting` (the API exits about a second later after a local-folder move; an SMB move keeps its mount) and `notice` (a mount note). The copy writes `libraries/`, `blobs/`, `backups/` and `exports/`; the databases stay in the app folder. Cancel works while files are being copied and removes the partial copy; after the copy is verified it can no longer be stopped.
3. **Library requests wait.** From the request until the job ends `app.state.moving` is set (`api/v1/moving.py`) and every library request answers `503 data_dir_moving` with `Retry-After: 5`, admins included. Still answering: health, the maintenance status, sign-in, `/me`, `/settings` and `/jobs*`, which touch only the site database. A second move request while one runs is `409 data_dir_job_running` and does not end the first one's protection.
4. **Verified before it takes effect.** After the copy, every copied folder must hold the same number of files and bytes as its source, else the job fails (`does not match the original`), the destination is cleaned and the old folder stays in use. Only then the databases are detached, their source paths rewritten (seconds, not the whole copy), the new runtime attached and probed, and `config.json` written. A failure at any step puts the old library back (`attach_runtime` runs in a `finally`).
5. **A crash never splits the library.** `data-dir-move.json` (app folder) is written before the copy (`phase: copying`), updated before the databases are rewritten (`rewriting`), and removed at the end. At start (`recover_data_dir_move`, before anything opens) a leftover journal is settled by where `config.json` points: at the new folder, the move had been verified, so the old files are deleted (`finished`); still at the old folder, the partial copy is removed and, if the rewrite had begun, source paths are pointed back (`rolled_back`). A journal that matches neither folder is left alone and logged. The old files are deleted last, after the new folder answers; if that deletion fails the journal stays and the next start finishes it.

Maintenance stays on afterwards; the admin turns it off after checking. Code: `datamove.py` (journal, recovery), `runtime.switch_data_dir`, `config.copy_data_dir` (progress, verify, hooks), `ImportJobRunner.start_move`.
## Tests

`tests/test_maintenance.py`: the guard for admin, member and anonymous; sign-in rules; `draining` then `on`; blocked job start while draining; an admin seeing another user's running job without file names; reason trimming; flag written by another process; unreadable flag; start from the environment; the command line. `tests/test_datamove.py` and the move tests in `tests/test_settings.py`: the gate, the job and its progress without file names, library requests waiting, cancel, a copy that does not match, the journal and its recovery in each phase, start-up recovery.
