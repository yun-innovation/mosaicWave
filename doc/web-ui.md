# Web UI

Layout and navigation of the Next.js web client (milestone 4). Stack and hosting: [architecture.md § Web client](architecture.md#web-client). Strings and locales: [i18n.md](i18n.md).

## Layout

After sign-in the page is one shell (`app/AppShell.tsx`):

- **Top bar** — menu button (phones only), the mosaicWave logo (links to Photos), a health warning shown only when `GET /api/v1/health` fails, the **Upload** button, and the account menu.
- **Account menu** — the avatar opens it: display name, role, a **Language** quick switch (same logic as Settings, `LocaleSelect.tsx`), a **Settings** link, and **Sign out**. Escape or a press outside closes it.
- **Sidebar** — Photos, Albums, Import, Activity, Backup and Maintenance (admin only), Settings. While maintenance mode is on an admin sees only Backup, Maintenance and Activity under a banner; everyone else gets a full-page alert ([maintenance.md](maintenance.md)). The current view has `aria-current="page"`.
- **Status bar** — the first thing in the main area on every view while a background job runs (Takeout import, thumbnails, site backup): job name, `processed / total`, the current file and a progress bar, with a link to Activity and a **Cancel job** button (one confirmation that says what stays; also on running rows in Activity and on the Maintenance page). When the job ends it shows the result (successes clear after a few seconds, failures stay until dismissed). `StatusBar.tsx`.
- **Main area** — one view at a time.

On narrow screens (`max-width: 48rem`) the sidebar becomes a slide-over panel over a dimmed backdrop. The menu button opens it; Escape, a backdrop tap, or choosing a view closes it. Motion is off under `prefers-reduced-motion`.

The sign-in screen and the first-admin setup screen do not use the shell.

## Views and URLs

Each view has a hash URL, so links, reload and Back / Forward work without a server fallback (FastAPI serves the static export with `StaticFiles(html=True)`).

| URL | View | File |
| --- | --- | --- |
| `#/photos` | Timeline of every photo and video, grouped by day or month, Load more | `TimelineView.tsx` (inside `page.tsx`) |
| `#/albums` | Album tiles and the create-album form | `AlbumsView.tsx` |
| `#/albums/{id}` | One album: back to all albums, Delete album, the album timeline | `AlbumsView.tsx` + `TimelineView.tsx` |
| `#/import` | Google Takeout: folder path or browser folder, Check folder, Start import | `ImportView.tsx` |
| `#/activity` | Job list (import, push, thumbs, site backup); read-only | `ActivityView.tsx` |
| `#/maintenance` | **Admin only.** The maintenance switch, the message shown to users and the current state ([maintenance.md](maintenance.md)) | `MaintenanceView.tsx` (alert page: `MaintenanceScreen.tsx`) |
| `#/backup` | **Admin only.** Back up now (optional thumbnails), progress, archive list with download and delete, and the offline restore command ([backup.md](backup.md)) | `BackupView.tsx` |
| `#/settings` | Language, **Generate thumbs**, data folder, shares, users and permissions (admin parts as before) | `SettingsPanel.tsx` |

An empty or unknown hash shows Photos. Routing is `app/hashRoute.ts`: `useRoute()` reads `location.hash` with `useSyncExternalStore` on `hashchange`, and navigation sets `location.hash` (one history entry per view). The file is not called `route.ts` because that name is reserved by the Next App Router.

The photo viewer (`AssetViewer.tsx`) is a dialog over the current view, not a URL. Escape closes it.

**Upload** opens a dialog (`UploadDialog.tsx`) from any view. It lists the caller's writable folder sources and shows a picker only when there is more than one.

## State that outlives a view

`page.tsx` owns what must survive switching views:

- Sign-in, health, and the locale effect.
- `useTakeoutImport` (`app/useTakeoutImport.ts`): the Takeout form state and the browser-folder push. Its `message` is only the Import form's own result; job progress never goes through it.
- `useActiveJob` (`app/useActiveJob.ts`, used by `useTakeoutImport`): follows the signed-in user's newest job through `GET /api/v1/jobs?limit=1` (every second while one runs, every few seconds otherwise) and feeds the status bar. It does not care what started the job, so a job started before a reload, a sign-out or in another tab is picked up again, and it stops polling while signed out. `busy` (buttons disabled everywhere) is true while the server runs any job. Limits: the server runs one job at a time for everyone, but the job list is per user, so a member's job blocks an admin's start (job-busy error) without showing in the admin's bar.
- Two revision counters. `libraryRev` goes up when assets change (import done, upload, removal); timelines and Activity reload. `albumsRev` goes up when albums change; the album list reloads.

Shared types and URL helpers (`thumbSrc`, `stillSrc`, `isVideo`) are in `app/types.ts`.

## Moved from the old single page

- **Generate thumbs** is in Settings (a Thumbnails section for every user), not the gallery header or Activity.
- **Upload** is in the top bar, not the gallery header.
- **Settings** is a view; it has no Close button.
- Job and progress messages stay in the language they were created in (see milestone 8 in [roadmap.md](roadmap.md)).
