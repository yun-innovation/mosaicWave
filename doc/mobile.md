# Mobile clients (milestone 14)

Design note. No app code and no server code yet: this page settles the technology, the repository, the contract between the repos, and the order of the work. The roadmap row is in [roadmap.md](roadmap.md); the older replica design is in [schema.md § Client replica](schema.md#client-replica-web--mobile).

## Decisions

| Topic | Decision |
| --- | --- |
| Technology | **Flutter** (Dart): one codebase for iOS and Android. Chosen over two native apps to keep one team-sized code base; the cost is that background work on iOS goes through plugins ([Upload](#upload-and-the-operating-system)). |
| Repository | **`mosaicWave-mobile`**, a **private GitHub repository** under `yun-innovation` to begin with (made public later only if the project decides to). Not created yet. The apps never live in this repo: this repo's packaging and CI stay free of Xcode, the Android SDK and Flutter. |
| Contract | The versioned **`/api/v1`**, the **OpenAPI** schema (`/openapi.json`) and the **Postman collection** published from this repo. An API change here updates them in the same PR ([process.md](process.md)). No generated SDK (the web does not use one either); the app has hand-written Dart models for the endpoints it uses. |
| Server stays unaware | The server never learns what client it is. No `if mobile` branches: an endpoint added "for the apps" must be a normal endpoint the web could use. |
| First app features | In the roadmap order: (1) sign in to a server by URL, (2) browse timeline and albums, (3) upload from the phone, (4) offline replica. The user chose browse before upload. |

## What the apps need from this repo

Everything the web does already works over plain HTTPS JSON. The gaps, each its own PR here, in this order:

1. **Published contract.** Commit a generated `openapi.json` (a script plus a test that fails when the committed file is stale) so the app repo can pin a version of it; keep Postman current. Document the compatibility rule: additive changes only inside `v1`.
2. **App sign-in.** Today the session is a signed cookie (`mosaicwave_session`, see `auth/session.py`). A Flutter client can keep a cookie jar, but a header is simpler and survives the OS killing the app. Plan: `POST /auth/login` with `{"token": true}` also returns the same signed session value in the body, and every endpoint accepts it as `Authorization: Bearer <value>` as well as the cookie. No second token system. Sliding renewal: `POST /auth/refresh` returns a fresh value while the current one is valid (the web needs no change). Still to decide: lifetime of an app session (longer than the browser's), and whether the admin can list and revoke sessions.
3. **Server discovery.** `GET /auth/status` already answers without a session (`platform`, `needs_setup`, locales). Add the API version and a minimum app version to it, so an old app can say "update the app" instead of failing.
4. **Resumable upload.** The client-upload session (`/import/takeout/push` family) is for a Takeout dump and runs the Takeout ingest at the end. A phone uploads single photos over a bad network, so it needs a normal upload endpoint: create an upload (`name`, `size`, `taken_at`, a client content hash), `PUT` chunks with offsets, `HEAD` to learn how much arrived, and a finish that runs the usual import for one file. Duplicates are answered by hash before any bytes are sent (the server already dedupes on content). Written as its own design section in this page before code.
5. **Lighter list calls** (only if the app needs them after real use): smaller thumbnails sizes, a changes-since call for the replica (`rev` and `deleted_at` already exist on every table), and ETag on thumbnails.

Not needed from this repo: push notifications, share links (milestone 11), a mobile-only API.

## The app, in outline

- **Sign in:** server URL, then username and password through `POST /auth/login` (the server checks them with the QNAP or local provider). The URL and session value live in the platform's secure storage. The app sends its language in `X-MosaicWave-Language` and shows dates in the account's `time_zone` and `use_dst`, the same rules as the web ([i18n.md](i18n.md)): the server stores and sends UTC, the client formats.
- **Browse:** the timeline is `GET /assets` with cursor paging, `GET /assets/months` for the scrubber, search through `q`, `taken_from` and `taken_to`; albums through `/albums`. Thumbnails are cached on disk by asset id.
- **Select many, export:** the same album and export endpoints as the web.
- **Offline replica:** metadata only, per [schema.md](schema.md#client-replica-web--mobile); pushes carry `base_rev`; a `409` means refetch and ask. Last in the order.

## Upload and the operating system

Uploading while the app is closed is limited by each OS, not by this repo. Android can run a long task through its work manager; iOS only gives short background windows and a system-managed background transfer. So the upload endpoint must be **resumable and idempotent** (a killed transfer picks up at the byte it reached; sending a photo twice never makes two). What the app does on each OS belongs in the app repo's own notes.

## Order of work

| Step | Where | What |
| --- | --- | --- |
| A | this repo | This design note, roadmap and status updated. |
| B | this repo | Create the private `mosaicWave-mobile` repository and its README (needs the owner's go-ahead; it is an external action). |
| C | this repo | `openapi.json` published and checked in CI; Postman current. |
| D | this repo | Bearer header, `token: true` on login, `/auth/refresh`. |
| E | app repo | Flutter skeleton: sign in, secure storage, language and time zone. |
| F | app repo | Timeline, scrubber, albums, search, select many. Needs only C and D. |
| G | this repo, then app repo | Resumable upload design section, endpoint, then the app's uploader. |
| H | both | Offline replica. |

Milestone 14 is marked done only when you say so.

## Open questions

- App session lifetime and whether sessions can be listed and revoked.
- Whether the upload endpoint also accepts a whole file in one request for small photos (probably yes, as chunk 0 of 1).
- Distribution: TestFlight and Play internal testing first; store listing is a later decision.
