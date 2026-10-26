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

Today **Move library** (Server page, `PUT /api/v1/settings`) runs in one long request while users keep working. It detaches the library, copies `libraries/`, `blobs/`, `backups/` and `exports/`, and re-attaches; requests that arrive in between fail, a crash mid-copy can leave the files split, and a multi-hundred-GB copy can outlast the HTTP request. Milestone 13 puts it under this mode:

- **Gate (PR 1, built):** the move is refused unless the state is `on` (`409 maintenance_required`; `draining`, a staged restore or a running job also refuse). The Server page disables Move library and links to the Maintenance page. The destination checks (absolute path, not occupied, writable, free space) run before any copy, as now.
- **Job (PR 2):** the copy is a cancellable job of kind `data_dir_move`: progress counts only, never file names, shown in the status bar and Activity. `data_dir` and `config.json` change only after the copy is complete and verified (file count and byte sizes per top-level folder). Cancelling or failing removes the partial copy and leaves the old folder in use.
- **Detached library (PR 1, built as `api/v1/moving.py`; PUT /settings sets `app.state.moving`):** while it is detached, every library request answers `503 data_dir_moving` with `Retry-After: 5`, admins included (milestone 12 still lets an admin into the app; this closes the window for the admin's own library). Still answering: health, maintenance status, sign-in, `/me`, `/settings` and `/jobs*`, which touch only the site database.
- **Crash safety (PR 2):** a journal `data-dir-move.json` in the app folder is written before anything is copied or deleted and removed at the end. At start, a leftover journal rolls back (partial copy removed, old folder kept) when the switch had not happened, or finishes deleting the old files when it had. The old files are deleted only after the new folder is attached and answering.
- **Afterwards:** maintenance stays on; the admin turns it off after checking.

## Tests

`tests/test_maintenance.py`: the guard for admin, member and anonymous; sign-in rules; `draining` then `on`; blocked job start while draining; an admin seeing another user's running job without file names; reason trimming; flag written by another process; unreadable flag; start from the environment; the command line.
