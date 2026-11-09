# Web UI

Layout and navigation of the Next.js web client (milestone 4). Stack and hosting: [architecture.md § Web client](architecture.md#web-client). Strings and locales: [i18n.md](i18n.md).

## Layout

After sign-in the page is one shell (`app/AppShell.tsx`):

- **Top bar** — menu button (phones only), the mosaicWave logo (links to Photos), a health warning shown only when `GET /api/v1/health` fails, the **Upload** button, and the account menu.
- **Account menu** — the avatar opens it: display name, role, a **Language** quick switch (same logic as Settings, `LocaleSelect.tsx`), a **Settings** link, and **Sign out**. Escape or a press outside closes it.
- **Sidebar** — two groups with a separator between them. Upper, for everyone: Photos, Albums, Import, Activity, Settings. Lower, admin only: Server, Backup, Maintenance (members have no lower group, so no separator). Settings is personal and the same for everyone; options for the whole server are on the admin Server page. While maintenance mode is on an admin sees only Server, Backup, Maintenance and Activity under a banner; everyone else gets a full-page alert ([maintenance.md](maintenance.md)). The current view has `aria-current="page"`.
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
| `#/albums/{id}` | One album: back to all albums, **Export album** (layout and zip size, then a job), Delete album, the album timeline | `AlbumsView.tsx` + `TimelineView.tsx` |
| `#/import` | Google Takeout: folder path or browser folder, Check folder, Start import | `ImportView.tsx` |
| `#/activity` | Job list (import, push, thumbs, site backup); read-only | `ActivityView.tsx` |
| `#/maintenance` | **Admin only.** The maintenance switch, the message shown to users and the current state ([maintenance.md](maintenance.md)) | `MaintenanceView.tsx` (alert page: `MaintenanceScreen.tsx`) |
| `#/backup` | **Admin only.** Back up now (optional thumbnails and zip size), progress, archive list with download, **Restore…** and delete, the restore source form (on the server with Browse, or upload from this device), the staged-restore box (Restart and apply / Discard), the last restore result, and the offline restore command ([backup.md](backup.md)) | `BackupView.tsx` |
| `#/server` | **Admin only.** Options for the whole server: app folder, current library folder, **Move library** (local folder, SMB or NAS, Save folder), and users (add a member). Move library is available only while maintenance mode is on (the form is disabled, with a link to the Maintenance page, until then); it runs as a job, with progress in MB and Cancel in the status bar, and the page reports the result and waits for the API when it restarts. Shown during maintenance | `ServerView.tsx` |
| `#/settings` | **Personal, the same for everyone (admins included):** language, **Generate thumbs**, **My library backup** (back up with an optional zip size, list with download / **Restore…** / delete, **Restore from a file**: on this device, or on the server for admins, with a confirmation first; `LibraryBackupPanel.tsx`, `RestoreSourceForm.tsx`), **Export my photos** (all photos as ordinary files, plain or Takeout layout, split by zip size, list of exports with per-file download and delete; `ExportPanel.tsx`) and a one-line library summary | `SettingsPanel.tsx` |

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

- **Section titles** on Settings, Server and Backup (`titled-sections`) are bright and semi-bold (`section-title`), a subsection title (`backup-heading`, the pending-restore box) is smaller, and each section after the first starts under a thin line (`divided`). Field labels inside a form stay dim.
- **Generate thumbs** is in Settings (a Thumbnails section for every user), not the gallery header or Activity.
- **Upload** is in the top bar, not the gallery header.
- **Settings** is a view; it has no Close button.
- Job and progress messages stay in the language they were created in (see milestone 8 in [roadmap.md](roadmap.md)).

## Theme (milestone 9, PR 1)

- **Dark** (default), **Light**, or **Follow this device** (`prefers-color-scheme`), chosen in Settings (`ThemeSelect.tsx`). It is a choice for the **browser**, kept in `localStorage` (`mosaicWave.theme`; dark is stored as no value), not on the account: a phone in the sun and a desktop at night want different themes. The server never sees it.
- `theme.ts` sets `data-theme="dark|light"` on `<html>`. A small script in `<head>` (`THEME_BOOT_SCRIPT`, `layout.tsx`) does the same before the first paint, so a light-theme user never sees a dark flash. While the choice is "Follow this device" the page follows the device when it flips.
- All colours come from CSS variables in `globals.css` (`:root` is dark, `:root[data-theme="light"]` overrides them). New CSS uses a variable, never a hex colour, except the photo viewer and video backdrop (`#000`, `#0a0a0c`) and the black overlays, which stay dark in both themes.
## Search (milestone 9, PR 3)

- A search row sits above the Photos list and above an album's list: a text box (file or folder name, any case, plain text: `%` and `_` are not wildcards), a **From** and a **To** date, **Search** and **Clear**. Nothing runs until Search is pressed (or Enter); Clear and changing album reset it.
- The text goes to `GET /api/v1/assets?q=`: a photo matches when its path (folders and file name, e.g. `Trip to Kyoto/IMG_0001.jpg`) contains it. `album_id` still narrows to the album. `q` is trimmed and at most 200 characters.
- The dates are calendar days in the user's **display zone** (the same zone the day headings use, so a search for 1 March finds what the page shows under 1 March). The web turns them into instants (`dateRangeInstants` in `format.ts`: start of **From**, start of the day after **To**) and sends `taken_from` (inclusive) and `taken_to` (exclusive). Either may be left empty. Photos with no date never match a date range; a name search alone still finds them.
- Unlike `before`, which only starts the list and is ignored once a cursor is sent, the search filters apply to every page. While a search is active the timeline scrubber and its "Showing ... and older" line are hidden, and an empty result says "No photos match this search."

- **Album list.** With two or more albums, the Albums page has a search box that filters the tiles by album name as you type (any case, plain text, in the browser; the list is already loaded, so no API call). An empty match says "No album has that name." Searching the photos inside one album is the search row above.

## Timeline scrubber (milestone 9, PR 2)

- A rail down the right edge of the Photos view and of an album stands for the whole timeline, newest at the top, with the years labelled and a dot where the page is now. Press or drag on it to see a month and let go to jump there; Arrow, Page and Home/End keys do the same. It is shown only when there are at least two months (or one month and undated photos) to jump between.
- The list is paged, so a jump does not scroll to a month that is not loaded: it **restarts the list at that month** (`GET /api/v1/assets?before=<instant>`, the instant the next month begins in the user's display zone, so the first photo is the newest of the chosen month) and scrolls to the top. A line above the list says "Showing March 2026 and older" with **Back to newest**. Newer photos are reached by the scrubber or that link, not by scrolling up. "Unknown date" jumps to the photos with no date (`before=0001-01-01T00:00:00Z`).
- The months come from `GET /api/v1/assets/months` (photos per month, newest first, and an `undated` count; the same visibility as the list, `album_id` narrows it). Months are grouped in the user's **display zone**: `time_zone` and `use_dst` query parameters, which default to the account's saved `user.time_zone` and `user.use_dst` (UTC when none), so the rail agrees with the day headings the page draws. The web sends them explicitly (`chosenTimeZone()` in `format.ts`), so a zone just picked in Settings counts before its save reaches the account, and asks again when the zone or the summer-time switch changes. Counts are not shown.
- Where the page is now comes from an `IntersectionObserver` on the day blocks (the first block near the top of the screen), not a scroll listener.