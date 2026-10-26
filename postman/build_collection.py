"""Emit mosaicWave Postman collection + environments. Run from repo: python postman/build_collection.py"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent

COOKIE_PRE = [
    "const token = pm.collectionVariables.get('mw_session');",
    "if (token) {",
    "  pm.request.headers.upsert({ key: 'Cookie', value: 'mw_session=' + token });",
    "}",
]

SAVE_COOKIE = [
    "function cookieValue() {",
    "  const parts = pm.response.headers.all()",
    "    .filter((h) => String(h.key).toLowerCase() === 'set-cookie')",
    "    .map((h) => String(h.value));",
    "  const raw = parts.join('\\n');",
    "  const m = raw.match(/mw_session=([^;]+)/);",
    "  return m ? m[1] : null;",
    "}",
    "const token = cookieValue();",
    "if (token) { pm.collectionVariables.set('mw_session', token); }",
    "if (pm.response.code === 200) {",
    "  try {",
    "    const body = pm.response.json();",
    "    if (body && body.id) { pm.collectionVariables.set('userId', body.id); }",
    "  } catch (e) {}",
    "}",
]

CLEAR_COOKIE = [
    "pm.collectionVariables.unset('mw_session');",
]


def ev(listen: str, lines: list[str]) -> dict:
    return {
        "listen": listen,
        "script": {"type": "text/javascript", "exec": lines},
    }


def hdr_json() -> list[dict]:
    return [{"key": "Content-Type", "value": "application/json"}]


def req(
    name: str,
    method: str,
    path: str,
    *,
    description: str = "",
    body: str | None = None,
    formdata: list[dict] | None = None,
    file_body: bool = False,
    query: list[dict] | None = None,
    tests: list[str] | None = None,
    headers: list[dict] | None = None,
) -> dict:
    raw = "{{baseUrl}}" + path
    url: dict | str
    if query:
        qs = "&".join(
            f"{q['key']}={q.get('value', '')}"
            for q in query
            if not q.get("disabled")
        )
        url = {
            "raw": raw + (("?" + qs) if qs else ""),
            "host": ["{{baseUrl}}"],
            "path": [p for p in path.strip("/").split("/") if p],
            "query": [
                {
                    "key": q["key"],
                    "value": q.get("value", ""),
                    **({"disabled": True} if q.get("disabled") else {}),
                    **({"description": q["description"]} if q.get("description") else {}),
                }
                for q in query
            ],
        }
    else:
        url = raw

    request: dict = {
        "method": method,
        "header": headers if headers is not None else (hdr_json() if body else []),
        "url": url,
        "description": description,
    }
    if body is not None:
        request["body"] = {
            "mode": "raw",
            "raw": body,
            "options": {"raw": {"language": "json"}},
        }
    if file_body:
        request["body"] = {"mode": "file", "file": {"src": ""}}
        request["header"] = [{"key": "Content-Type", "value": "application/octet-stream"}]
    if formdata is not None:
        request["body"] = {"mode": "formdata", "formdata": formdata}
        request["header"] = []

    item: dict = {"name": name, "request": request}
    if tests:
        item["event"] = [ev("test", tests)]
    return item


def folder(name: str, items: list[dict], description: str = "") -> dict:
    out: dict = {"name": name, "item": items}
    if description:
        out["description"] = description
    return out


SAVE_ASSET = [
    "if (pm.response.code === 200) {",
    "  const b = pm.response.json();",
    "  if (b.items && b.items[0]) { pm.collectionVariables.set('assetId', b.items[0].id); }",
    "  if (b.next_cursor) { pm.collectionVariables.set('assetCursor', b.next_cursor); }",
    "}",
]

SAVE_ALBUM = [
    "if (pm.response.code === 200) {",
    "  const b = pm.response.json();",
    "  if (Array.isArray(b) && b[0]) {",
    "    pm.collectionVariables.set('albumId', b[0].id);",
    "    if (b[0].rev != null) { pm.collectionVariables.set('albumRev', String(b[0].rev)); }",
    "  } else if (b && b.id) {",
    "    pm.collectionVariables.set('albumId', b.id);",
    "    if (b.rev != null) { pm.collectionVariables.set('albumRev', String(b.rev)); }",
    "  }",
    "}",
]

SAVE_SOURCE = [
    "if (pm.response.code === 200) {",
    "  const rows = pm.response.json();",
    "  const list = Array.isArray(rows) ? rows : (rows && rows.id ? [rows] : []);",
    "  const mine = list.find((r) => r.owned) || list[0];",
    "  if (mine) { pm.collectionVariables.set('sourceId', mine.id); }",
    "}",
]

SAVE_USERS = [
    "if (pm.response.code === 200) {",
    "  const rows = pm.response.json();",
    "  const member = rows.find((u) => u.role === 'member') || rows[0];",
    "  if (member) { pm.collectionVariables.set('userId', member.id); }",
    "}",
]

SAVE_USER = [
    "if (pm.response.code === 200) {",
    "  const b = pm.response.json();",
    "  if (b.id) { pm.collectionVariables.set('userId', b.id); }",
    "}",
]

SAVE_SYNC = [
    "if (pm.response.code === 200) {",
    "  const b = pm.response.json();",
    "  if (b.server_rev != null) { pm.collectionVariables.set('sinceRev', String(b.server_rev)); }",
    "  if (b.next_cursor) { pm.collectionVariables.set('syncCursor', b.next_cursor); }",
    "}",
]

SAVE_TOMBSTONE_PULL = SAVE_SYNC + [
    "if (pm.response.code === 200) {",
    "  const b = pm.response.json();",
    "  const id = pm.collectionVariables.get('albumId');",
    "  const hit = (b.albums || []).find((a) => a.id === id);",
    "  pm.test('deleted album is in pull as a tombstone', function () {",
    "    pm.expect(hit, 'album id among albums').to.exist;",
    "    pm.expect(hit.deleted_at, 'deleted_at').to.not.equal(null);",
    "  });",
    "}",
]

EXPECT_409 = [
    "pm.test('stale base_rev is 409', function () {",
    "  pm.response.to.have.status(409);",
    "});",
]

SAVE_LIBRARY_BACKUP = [
    "var b = pm.response.json();",
    "if (Array.isArray(b) && b[0]) { pm.collectionVariables.set('libraryBackupName', b[0].name); }",
]
SAVE_EXPORT = [
    "var b = pm.response.json();",
    "if (Array.isArray(b) && b[0]) {",
    "  pm.collectionVariables.set('exportName', b[0].name);",
    "  if (b[0].files && b[0].files[0]) { pm.collectionVariables.set('exportFile', b[0].files[0].path); }",
    "}",
]
SAVE_BACKUP = [
    "if (pm.response.code === 200) {",
    "  const b = pm.response.json();",
    "  if (Array.isArray(b) && b[0]) { pm.collectionVariables.set('backupName', b[0].name); }",
    "}",
]

SAVE_JOB = [
    "if (pm.response.code === 200) {",
    "  const b = pm.response.json();",
    "  const id = b.id || (b.items && b.items[0] && b.items[0].id);",
    "  if (id) { pm.collectionVariables.set('jobId', id); }",
    "}",
]

collection = {
    "info": {
        "name": "mosaicWave /api/v1",
        "description": """Hand-written Postman collection for mosaicWave. Not a generated client.

**Import:** Postman → Import → `postman/mosaicwave.postman_collection.json` and an environment JSON from this folder. Create a Postman workspace if you want (File → New → Workspace); workspaces are not a file format.

**Auth:** `POST /auth/login` sets httponly cookie `mw_session`. The collection copies it into variable `mw_session` and sends `Cookie` on later requests. You can also use Postman’s cookie jar.

**Order that fills ids:** Login → List sources → List assets → List users. Then open thumb/file using `{{assetId}}` / `{{sourceId}}`. Share: owner **Set grants** with a member `userId` (`read` or `write`).

**Share:** `PUT /sources/{id}/grants` is the owner’s replacement list. Grantee keeps their own library and also sees the shared one. Admin has no bypass.

**Album sync:** Pull is `GET /sync/changes` (`since_rev`). Push is `/albums` with `base_rev` (the album `rev`, not `server_rev`). Folder **Sync replica** is create-`id`, membership `base_rev`, delete + tombstone pull. Do not POST the pull JSON back.

Hit FastAPI directly (`http://127.0.0.1:8000` debug, `:8090` MSI/QPKG), not the Next.js `:3000` proxy, unless you intend to test the rewrite.

**Backups (admin):** folder **Backups**. Start a site backup, poll the job in **Jobs**, then List backups saves `{{backupName}}` for Download / Delete. Restore: turn on maintenance, Stage restore (a job), poll it, then Apply restore (restarts the server) and read Restore state. `python -m mosaicwave.restore` still works offline.

**My library backup (any signed-in user):** folder **My library backups**. Back up my library starts a job; List my backups saves `{{libraryBackupName}}` for Download / Delete / Restore. Restore replaces only your own library while the server runs (a job; your previous library is deleted once the restore succeeds, so back up first).

**My exports (any signed-in user):** folder **My exports**. Export my photos starts a job that writes your photos as ordinary files (plain or Google Takeout layout, split into zips by size; a file bigger than the size is left unzipped beside them). List my exports saves `{{exportName}}` and `{{exportFile}}` for Download / Delete. An export is not a backup.

**Maintenance:** folder **Maintenance**. Status is public; Turn on/off need an admin session. While it is on, other users get `503 maintenance`.

OpenAPI at `{{baseUrl}}/docs` stays the living spec; this collection is a snapshot for manual tests.""",
        "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
    },
    "variable": [
        {"key": "baseUrl", "value": "http://127.0.0.1:8000"},
        {"key": "username", "value": "admin"},
        {"key": "password", "value": ""},
        {"key": "mw_session", "value": ""},
        {"key": "userId", "value": ""},
        {"key": "sourceId", "value": ""},
        {"key": "assetId", "value": ""},
        {"key": "assetCursor", "value": ""},
        {"key": "jobId", "value": ""},
        {"key": "backupName", "value": ""},
        {"key": "libraryBackupName", "value": ""},
        {"key": "exportName", "value": ""},
        {"key": "exportFile", "value": ""},
        {"key": "sinceRev", "value": "0"},
        {"key": "syncCursor", "value": ""},
        {"key": "albumId", "value": ""},
        {"key": "albumRev", "value": ""},
        {"key": "replicaAlbumId", "value": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"},
        {"key": "sourcePath", "value": r"C:\Photos"},
        {"key": "takeoutPath", "value": r"C:\Takeout"},
        {"key": "destPath", "value": r"C:\Photos"},
        {"key": "fsPath", "value": ""},
        {"key": "dataDir", "value": r"D:\mosaicWave-data"},
        {"key": "memberUsername", "value": "pat"},
        {"key": "memberPassword", "value": "memberpass"},
        {"key": "memberDisplayName", "value": "Pat"},
    ],
    "event": [ev("prerequest", COOKIE_PRE)],
    "item": [
        folder(
            "Health",
            [
                req(
                    "Health",
                    "GET",
                    "/api/v1/health",
                    description="No session. Process liveness.",
                )
            ],
        ),
        folder(
            "Auth",
            [
                req(
                    "Auth status",
                    "GET",
                    "/api/v1/auth/status",
                    description="platform, needs_setup, me if cookied.",
                ),
                req(
                    "First-run setup",
                    "POST",
                    "/api/v1/auth/setup",
                    description="Standalone only, empty DB. Creates first admin and sets mw_session. 409 if already set up.",
                    body="{\n  \"username\": \"{{username}}\",\n  \"password\": \"{{password}}\",\n  \"display_name\": \"Admin\"\n}",
                    tests=SAVE_COOKIE,
                ),
                req(
                    "Login",
                    "POST",
                    "/api/v1/auth/login",
                    description="Standalone: username + password. QNAP: same, or empty body if NAS session cookies are already in the jar.",
                    body="{\n  \"username\": \"{{username}}\",\n  \"password\": \"{{password}}\"\n}",
                    tests=SAVE_COOKIE,
                ),
                req("Me", "GET", "/api/v1/me", description="Current user, role, private library ids, locale."),
                req(
                    "Set language",
                    "PATCH",
                    "/api/v1/me",
                    description="locale: en, zh-Hant, zh-Hans, or null for automatic (follow the browser).",
                    body="{\n  \"locale\": \"zh-Hant\"\n}",
                ),
                req(
                    "Logout",
                    "POST",
                    "/api/v1/auth/logout",
                    description="Clears mw_session cookie.",
                    tests=CLEAR_COOKIE,
                ),
            ],
        ),
        folder(
            "Users (admin)",
            [
                req(
                    "List users",
                    "GET",
                    "/api/v1/users",
                    description="Any signed-in user (for share picker). Saves {{userId}} (prefers a member).",
                    tests=SAVE_USERS,
                ),
                req(
                    "Create member",
                    "PUT",
                    "/api/v1/users",
                    description="Standalone only. Omit id to create. Password min 8. Does not share libraries.",
                    body="{\n  \"username\": \"{{memberUsername}}\",\n  \"password\": \"{{memberPassword}}\",\n  \"display_name\": \"{{memberDisplayName}}\",\n  \"role\": \"member\"\n}",
                    tests=SAVE_USER,
                ),
                req(
                    "Update user",
                    "PUT",
                    "/api/v1/users",
                    description="Pass id to update. Password optional.",
                    body="{\n  \"id\": \"{{userId}}\",\n  \"username\": \"{{memberUsername}}\",\n  \"display_name\": \"{{memberDisplayName}}\",\n  \"role\": \"member\"\n}",
                    tests=SAVE_USER,
                ),
            ],
            description="Local users on standalone. QNAP rejects create/update. List is available to every signed-in user.",
        ),
        folder(
            "Settings (admin)",
            [
                req(
                    "Get host settings",
                    "GET",
                    "/api/v1/settings",
                    description="Admin. data_dir (library), data_dir_smb (durable smb:// when on a NAS), data_dir_mount (macOS share-root dest), smb_user (from that URL; password is not returned), app_dir / default_data_dir (config and session), env_override (true when MOSAICWAVE_DATA_DIR is set).",
                ),
                req(
                    "Set data folder",
                    "PUT",
                    "/api/v1/settings",
                    description="Admin. Needs maintenance mode on (409 maintenance_required). Starts a cancellable data_dir_move job and returns it (same shape as GET /jobs/{id}; poll that, result has restarting and notice); library requests answer 503 data_dir_moving until it ends. Moves libraries/, blobs/, backups/ and exports/. library.db stays in the app folder. session.key stays in the app folder. Stores data_dir_smb and optional data_dir_mount; remounts that URL at that dest after reboot. Optional smb_user / smb_password: nsmb.conf username= if missing; System keychain -A -U only when a password is typed. Returning to app_dir replaces leftover libraries/blobs there. 409 if env is set, maintenance is not on, a job is running, or dest already has photos/thumbs (not when dest is the app folder).",
                    body="{\n  \"data_dir\": \"{{dataDir}}\"\n}",
                ),
            ],
        ),
        folder(
            "Library",
            [
                req(
                    "List sources",
                    "GET",
                    "/api/v1/sources",
                    description="Own library first (owned=true), then libraries shared with you. Saves {{sourceId}} (owned).",
                    tests=SAVE_SOURCE,
                ),
                req(
                    "Add folder source",
                    "POST",
                    "/api/v1/sources",
                    description="409 — each user already has one library. Share uses grants, not a second source.",
                    body="{\n  \"path\": \"{{sourcePath}}\",\n  \"kind\": \"folder\"\n}",
                ),
                req(
                    "Get grants",
                    "GET",
                    "/api/v1/sources/{{sourceId}}/grants",
                    description="Owner only. Live grants (read|write). 404 if you do not own this source.",
                ),
                req(
                    "Set grants",
                    "PUT",
                    "/api/v1/sources/{{sourceId}}/grants",
                    description="Owner only. Replaces live grants. {{userId}} from List users. Empty array revokes all. Cannot grant to yourself.",
                    body="{\n  \"grants\": [\n    { \"user_id\": \"{{userId}}\", \"perm\": \"read\" }\n  ]\n}",
                ),
                req(
                    "Scan source",
                    "POST",
                    "/api/v1/sources/{{sourceId}}/scan",
                    description="Re-index the caller's library.",
                    tests=SAVE_SOURCE,
                ),
                req(
                    "Delete source",
                    "DELETE",
                    "/api/v1/sources/{{sourceId}}",
                    description="400 — cannot delete your library.",
                ),
            ],
        ),
        folder(
            "Assets",
            [
                req(
                    "List assets",
                    "GET",
                    "/api/v1/assets",
                    description="Cursor pagination. Saves {{assetId}} and {{assetCursor}}.",
                    query=[
                        {"key": "limit", "value": "50", "description": "1–200"},
                        {
                            "key": "cursor",
                            "value": "{{assetCursor}}",
                            "disabled": True,
                            "description": "From previous next_cursor",
                        },
                        {
                            "key": "album_id",
                            "value": "{{albumId}}",
                            "disabled": True,
                            "description": "Only assets in this album",
                        },
                    ],
                    tests=SAVE_ASSET,
                ),
                req("Asset metadata", "GET", "/api/v1/assets/{{assetId}}"),
                req("Thumb", "GET", "/api/v1/assets/{{assetId}}/thumb", description="JPEG. May generate on first hit."),
                req(
                    "Preview",
                    "GET",
                    "/api/v1/assets/{{assetId}}/preview",
                    description="JPEG display for HEIC/TIFF/DNG and similar.",
                ),
                req(
                    "Original file",
                    "GET",
                    "/api/v1/assets/{{assetId}}/file",
                    description="Original media. The gallery player skips by time.",
                    query=[{"key": "download", "value": "false", "disabled": True}],
                    headers=[{"key": "Range", "value": "bytes=0-1023", "disabled": True}],
                ),
                req(
                    "Original download",
                    "GET",
                    "/api/v1/assets/{{assetId}}/file",
                    description="Content-Disposition attachment.",
                    query=[{"key": "download", "value": "true"}],
                ),
                req(
                    "Upload",
                    "POST",
                    "/api/v1/assets",
                    description="multipart. Uploads into the caller's library. Pick a file in Postman (src is empty on purpose).",
                    formdata=[
                        {"key": "source_id", "value": "{{sourceId}}", "type": "text"},
                        {
                            "key": "file",
                            "type": "file",
                            "src": "",
                            "description": "Choose an image or video in Postman",
                        },
                    ],
                    tests=[
                        "if (pm.response.code === 200) {",
                        "  const b = pm.response.json();",
                        "  if (b.id) { pm.collectionVariables.set('assetId', b.id); }",
                        "}",
                    ],
                ),
                req(
                    "Generate thumbs",
                    "POST",
                    "/api/v1/thumbs/generate",
                    description="Admin. Starts thumb_batch job. 409 if another job is running.",
                    tests=SAVE_JOB,
                ),
            ],
        ),
        folder(
            "Albums",
            [
                req(
                    "List albums",
                    "GET",
                    "/api/v1/albums",
                    description="Virtual albums in the caller's library (Takeout + manual).",
                    tests=SAVE_ALBUM,
                ),
                req(
                    "Create album",
                    "POST",
                    "/api/v1/albums",
                    description="Metadata only; originals stay in FileStore. Optional `id` (UUID) for a replica-created album. 409 if the name exists.",
                    body='{\n  "name": "Weekend"\n}',
                    tests=SAVE_ALBUM,
                ),
                req("Get album", "GET", "/api/v1/albums/{{albumId}}"),
                req(
                    "Rename album",
                    "PATCH",
                    "/api/v1/albums/{{albumId}}",
                    description="name and/or cover_asset_id. Replica: {{albumRev}} as base_rev (not sinceRev). Omit base_rev for live UI.",
                    body='{\n  "name": "Holiday"\n}',
                    tests=SAVE_ALBUM,
                ),
                req(
                    "Add assets",
                    "POST",
                    "/api/v1/albums/{{albumId}}/assets",
                    description="Optional `base_rev` for replica push (409 if stale).",
                    body='{\n  "asset_ids": ["{{assetId}}"]\n}',
                    tests=SAVE_ALBUM,
                ),
                req(
                    "Remove asset",
                    "DELETE",
                    "/api/v1/albums/{{albumId}}/assets/{{assetId}}",
                    description="Optional query base_rev for replica push.",
                    query=[{"key": "base_rev", "value": "{{sinceRev}}", "disabled": True}],
                ),
                req(
                    "Delete album",
                    "DELETE",
                    "/api/v1/albums/{{albumId}}",
                    description="Tombstone the album and its memberships. Same name can be created again (same id). Replica: query base_rev; 409 if stale.",
                    query=[{"key": "base_rev", "value": "{{sinceRev}}", "disabled": True}],
                ),
            ],
        ),
        folder(
            "Sync",
            [
                req(
                    "Pull changes",
                    "GET",
                    "/api/v1/sync/changes",
                    description="Metadata replica pull. Saves {{sinceRev}} from server_rev. Album push is /albums with base_rev (not this URL).",
                    query=[
                        {"key": "since_rev", "value": "{{sinceRev}}", "description": "0 for full pull"},
                        {"key": "limit", "value": "200", "description": "1–500"},
                        {
                            "key": "cursor",
                            "value": "{{syncCursor}}",
                            "disabled": True,
                            "description": "From previous next_cursor",
                        },
                    ],
                    tests=SAVE_SYNC,
                )
            ],
        ),
        folder(
            "Sync replica",
            [
                req(
                    "1 Pull (watermark)",
                    "GET",
                    "/api/v1/sync/changes",
                    description="Run first. Saves {{sinceRev}}. Later pull after delete uses this so only new revs return.",
                    query=[
                        {"key": "since_rev", "value": "0", "description": "Full pull once"},
                        {"key": "limit", "value": "200"},
                    ],
                    tests=SAVE_SYNC,
                ),
                req(
                    "2 Create album with client id",
                    "POST",
                    "/api/v1/albums",
                    description="Replica-created UUID. Idempotent if you send the same id+name again. Saves {{albumId}} and {{albumRev}}.",
                    body='{\n  "id": "{{replicaAlbumId}}",\n  "name": "Postman replica"\n}',
                    tests=SAVE_ALBUM,
                ),
                req(
                    "3 Add assets with base_rev",
                    "POST",
                    "/api/v1/albums/{{albumId}}/assets",
                    description="Needs {{assetId}} from List assets. {{albumRev}} from step 2. 200 bumps rev.",
                    body='{\n  "asset_ids": ["{{assetId}}"],\n  "base_rev": {{albumRev}}\n}',
                    tests=SAVE_ALBUM,
                ),
                req(
                    "4 Add assets stale base_rev (expect 409)",
                    "POST",
                    "/api/v1/albums/{{albumId}}/assets",
                    description="Outdated JSON: base_rev 0 never matches a real album rev.",
                    body='{\n  "asset_ids": ["{{assetId}}"],\n  "base_rev": 0\n}',
                    tests=EXPECT_409,
                ),
                req(
                    "5 Delete album with base_rev",
                    "DELETE",
                    "/api/v1/albums/{{albumId}}",
                    description="Tombstone. Use {{albumRev}} from step 3 (after the 200 add).",
                    query=[{"key": "base_rev", "value": "{{albumRev}}", "description": "Must match current album rev"}],
                ),
                req(
                    "6 Pull tombstone",
                    "GET",
                    "/api/v1/sync/changes",
                    description="since_rev from step 1. Asserts the deleted album appears with deleted_at set.",
                    query=[
                        {"key": "since_rev", "value": "{{sinceRev}}"},
                        {"key": "limit", "value": "200"},
                    ],
                    tests=SAVE_TOMBSTONE_PULL,
                ),
            ],
            description="Extra album sync (API already in). Login + List assets first. Order 1–6. base_rev is the album rev, not server_rev. Do not POST the pull JSON.",
        ),
        folder(
            "Filesystem",
            [
                req(
                    "List host folder",
                    "GET",
                    "/api/v1/fs/list",
                    description="Browse folders on the API host (Takeout picker). Other users' library dirs are hidden.",
                    query=[
                        {
                            "key": "path",
                            "value": "{{fsPath}}",
                            "disabled": True,
                            "description": "Absolute path on the server",
                        }
                    ],
                )
            ],
        ),
        folder(
            "Takeout",
            [
                req(
                    "Inspect dump",
                    "POST",
                    "/api/v1/import/takeout/inspect",
                    description="Verify zip parts / extract. path is on the API host.",
                    body="{\n  \"path\": \"{{takeoutPath}}\"\n}",
                ),
                req(
                    "Start import",
                    "POST",
                    "/api/v1/import/takeout",
                    description="Copy unique files into the caller's library. dest is ignored. Dump may be deleted after. 409 if a job is already running. Host path only.",
                    body="{\n  \"path\": \"{{takeoutPath}}\"\n}",
                    tests=SAVE_JOB,
                ),
                req(
                    "Start client push",
                    "POST",
                    "/api/v1/import/takeout/push",
                    description="Mobile / this-device dump. Saves {{jobId}}. Then POST .../file?relative_path= for each dump path and POST .../finish.",
                    body="{\n  \"file_count\": 1\n}",
                    tests=SAVE_JOB,
                ),
                req(
                    "Upload dump file",
                    "POST",
                    "/api/v1/import/takeout/push/{{jobId}}/file",
                    description="Query relative_path + raw file body (not multipart). Path is inside the dump folder, not a host path.",
                    query=[
                        {
                            "key": "relative_path",
                            "value": "Google Photos/Photos from 2024/IMG_1234.jpg",
                            "description": "Path inside the dump folder",
                        }
                    ],
                    file_body=True,
                    tests=SAVE_JOB,
                ),
                req(
                    "Finish client push",
                    "POST",
                    "/api/v1/import/takeout/push/{{jobId}}/finish",
                    description="Run hash-dedup import on uploaded files; delete staging.",
                    tests=SAVE_JOB,
                ),
                req(
                    "Cancel client push",
                    "POST",
                    "/api/v1/import/takeout/push/{{jobId}}/cancel",
                    description="Abort while files are still arriving.",
                    tests=SAVE_JOB,
                ),
                req(
                    "Cancel job",
                    "POST",
                    "/api/v1/jobs/{{jobId}}/cancel",
                    description="Stop a running job (thumbnails, site backup, Takeout import). Owner or admin; others get 404. The reply still says running: it ends as cancelled at the next check, so poll GET /jobs/{{jobId}}. 409 job_not_running if it already ended. Finished items stay; a cancelled backup leaves no archive.",
                ),
                req(
                    "Import status",
                    "GET",
                    "/api/v1/import/takeout/status",
                    description="Latest Takeout job. Prefer GET /jobs/{{jobId}}.",
                    tests=SAVE_JOB,
                ),
            ],
        ),
        folder(
            "Jobs",
            [
                req(
                    "List jobs",
                    "GET",
                    "/api/v1/jobs",
                    description="Saves {{jobId}} from the first item.",
                    query=[
                        {"key": "limit", "value": "50"},
                        {
                            "key": "kind",
                            "value": "takeout_import",
                            "disabled": True,
                            "description": "Optional filter",
                        },
                    ],
                    tests=SAVE_JOB,
                ),
                req("Get job", "GET", "/api/v1/jobs/{{jobId}}"),
                req("Job events", "GET", "/api/v1/jobs/{{jobId}}/events"),
            ],
        ),
        folder(
            "Backups",
            [
                req(
                    "Start site backup",
                    "POST",
                    "/api/v1/backups",
                    description="Admin only. Starts a job (kind site_backup) and saves {{jobId}}; poll GET /jobs/{{jobId}}. 409 if another job is running. Archive lands in the app folder's backups/. include_thumbs adds thumbnails (can be regenerated). split_gb (0.0001 to 1024) writes a backup bigger than that as several zips, mosaicwave-site-<time>-001.zip, -002.zip and so on, which must stay together; empty is one zip.",
                    body="{\n  \"include_thumbs\": false\n}",
                    tests=SAVE_JOB,
                ),
                req(
                    "List backups",
                    "GET",
                    "/api/v1/backups",
                    description="Admin only. Newest first. Saves {{backupName}} from the first item.",
                    tests=SAVE_BACKUP,
                ),
                req(
                    "Download backup",
                    "GET",
                    "/api/v1/backups/{{backupName}}",
                    description="Admin only. The zip contains password hashes and every user's originals; store it like the data folder. Use Send and Download in Postman.",
                ),
                req(
                    "Delete backup",
                    "DELETE",
                    "/api/v1/backups/{{backupName}}",
                    description="Admin only. 404 backup_not_found if the name does not exist.",
                ),
                req(
                    "Stage restore",
                    "POST",
                    "/api/v1/backups/{{backupName}}/restore",
                    description="Admin only. Needs maintenance mode on (409 maintenance_required) and no running job (409 job_running; 409 restore_pending while a staged restore waits). Starts a job (kind site_restore, cancellable) that unpacks and checks the archive next to the live data; saves {{jobId}}. Nothing live changes until Apply.",
                    tests=SAVE_JOB,
                ),
                req(
                    "Restore from a server path",
                    "POST",
                    "/api/v1/backups/restore",
                    description="Admin only. Body {path}: a site backup .zip anywhere on the server (a share, a USB drive); it is restored from there, nothing is copied. Same rules as Stage restore (maintenance_required, job_running, restore_pending). 400 backup_path_required, backup_not_a_file or backup_invalid (not a usable site backup) start no job. Saves {{jobId}}.",
                    body="{\n  \"path\": \"/share/backups/mosaicwave-site-20261021-093000.zip\"\n}",
                    tests=SAVE_JOB,
                ),
                req(
                    "Upload backup (from the device)",
                    "PUT",
                    "/api/v1/backups/upload",
                    description="Admin only. Raw zip body (Send and Select File). Streamed to the app folder's backups/ and checked (manifest and zip index only); stored as mosaicwave-site-<time inside the archive>.zip and returned as listed. The same file twice is one archive; a different file with the same time gets the next free second. 400 backup_invalid (deleted), 413 upload_too_large, 507 backup_no_space, 409 restore_pending or job_running. A part of a split backup (…-NNN.zip) is stored under its own number: upload every part, in any order, then use Stage restore with the first part's name (400 backup_parts_missing lists what is not there). The response lists the backup as one entry (parts, part_files, missing).",
                    file_body=True,
                ),
                req(
                    "Restore state",
                    "GET",
                    "/api/v1/backups/restore",
                    description="Admin only. pending is the staged restore waiting to be applied (archive, summary, upgrade). result is what the last start-up swap did (status done or error). The replaced data is deleted once the new data is in place, so kept is empty.",
                ),
                req(
                    "Apply restore",
                    "POST",
                    "/api/v1/backups/restore/apply",
                    description="Admin only. Restarts the server; the staged restore is swapped in before anything opens. 409 restore_not_staged, maintenance_required or job_running. restarting is true when a restart was scheduled (a supervisor or start script must bring the API back). Sign in again afterwards if the session is refused, then Restore state shows the result.",
                ),
                req(
                    "Discard restore",
                    "DELETE",
                    "/api/v1/backups/restore",
                    description="Admin only. Throws a staged restore away and forgets the last result.",
                ),
            ],
        ),
        folder(
            "My library backups",
            [
                req(
                    "Back up my library",
                    "POST",
                    "/api/v1/me/backups",
                    description="Any signed-in user. Starts a job (kind library_backup, cancellable; poll it in Jobs) that writes mosaicwave-library-<time>.zip (or -001.zip, -002.zip … when the optional body split_gb is smaller than the library; keep the parts together) under your own backups folder: your library database and originals, no thumbnails. 409 job_running while another job runs. Saves {{jobId}}.",
                    tests=SAVE_JOB,
                ),
                req(
                    "List my backups",
                    "GET",
                    "/api/v1/me/backups",
                    description="Only your own archives, newest first. Saves {{libraryBackupName}} from the first item.",
                    tests=SAVE_LIBRARY_BACKUP,
                ),
                req(
                    "Download my backup",
                    "GET",
                    "/api/v1/me/backups/{{libraryBackupName}}",
                    description="404 backup_not_found for a name that is not yours. Use Send and Download in Postman.",
                ),
                req(
                    "Delete my backup",
                    "DELETE",
                    "/api/v1/me/backups/{{libraryBackupName}}",
                    description="404 backup_not_found if the name does not exist in your folder.",
                ),
                req(
                    "Upload my backup (from the device)",
                    "PUT",
                    "/api/v1/me/backups/upload",
                    description="Raw zip body (Send and Select File). Streamed to your backups folder and checked; stored as mosaicwave-library-<time inside the archive>.zip and returned as listed. 400 backup_wrong_kind (a site backup), backup_invalid, 413 upload_too_large, 507 backup_no_space, 409 job_running or restore_pending. The archive may come from another account; restoring remaps it to you.",
                    file_body=True,
                ),
                req(
                    "Restore my library from a server path",
                    "POST",
                    "/api/v1/me/backups/restore",
                    description="Admin only (403 admin_only for members). Body {path}: a library backup .zip anywhere on the server; restored from there, nothing is copied. 400 backup_path_required, backup_not_a_file, backup_wrong_kind (a site backup) or backup_invalid start no job; 403 folder_not_allowed for a path under another user's library or archive folder. Then a library_restore job as for Restore my library. Saves {{jobId}}.",
                    body="{\n  \"path\": \"/share/backups/mosaicwave-library-20261021-093000.zip\"\n}",
                    tests=SAVE_JOB,
                ),
                req(
                    "Restore my library",
                    "POST",
                    "/api/v1/me/backups/{{libraryBackupName}}/restore",
                    description="Replaces your library with the backup while the server runs: a job (kind library_restore) that unpacks and checks it, then swaps your database and originals (cancellable until the swap starts). Your previous library is deleted once the restore succeeds (back it up first); a failed restore changes nothing. Other users are not affected; your own requests get 503 library_restoring for a moment during the swap. 409 job_running or restore_pending. Saves {{jobId}}.",
                    tests=SAVE_JOB,
                ),
            ],
        ),
        folder(
            "My exports",
            [
                req(
                    "Export all my photos",
                    "POST",
                    "/api/v1/me/exports",
                    description="Any signed-in user. Starts a job (kind export, cancellable; poll it in Jobs) that writes your photos as ordinary files under your own exports folder. Body: all (whole library) or album_ids and/or asset_ids; layout plain or takeout (Google Takeout tree with .json sidecars); split_gb (empty for one zip) - a file bigger than that is not zipped but saved beside the zips. 400 export_nothing_selected, export_scope or export_empty, 404 album_not_found, 409 job_running. Saves {{jobId}}.",
                    body="{\n  \"all\": true,\n  \"layout\": \"takeout\",\n  \"split_gb\": 4\n}",
                    tests=SAVE_JOB,
                ),
                req(
                    "Export one album",
                    "POST",
                    "/api/v1/me/exports",
                    description="Same job for chosen albums (and/or photo ids). Needs {{albumId}} from List albums. A photo in an exported album is written once, into that album's folder. Saves {{jobId}}.",
                    body="{\n  \"album_ids\": [\"{{albumId}}\"],\n  \"layout\": \"plain\",\n  \"split_gb\": null\n}",
                    tests=SAVE_JOB,
                ),
                req(
                    "List my exports",
                    "GET",
                    "/api/v1/me/exports",
                    description="Only your own exports, newest first, each with layout, counts, size and files ({path, size, zipped}). Saves {{exportName}} and {{exportFile}} from the first export.",
                    tests=SAVE_EXPORT,
                ),
                req(
                    "Download an export file",
                    "GET",
                    "/api/v1/me/exports/{{exportName}}/files/{{exportFile}}",
                    description="One zip part, or a file that was too big to zip. 404 export_not_found for a name or path that is not yours or not in the export. Use Send and Download in Postman.",
                ),
                req(
                    "Delete my export",
                    "DELETE",
                    "/api/v1/me/exports/{{exportName}}",
                    description="Removes the whole export (every zip and unzipped file). 404 export_not_found if it is not yours.",
                ),
            ],
        ),
        folder(
            "Maintenance",
            [
                req(
                    "Maintenance status",
                    "GET",
                    "/api/v1/maintenance",
                    description="Public (no sign-in). state is off, draining (on, but a job is still running) or on, with the reason and since. Works during maintenance, unlike most other requests.",
                ),
                req(
                    "Running job (any user)",
                    "GET",
                    "/api/v1/jobs/running",
                    description="Admin only. The one job running on the server, whoever started it: kind, owner, progress and start time, but no file names. This is the job maintenance mode waits for while the state is draining. 403 for members.",
                ),
                req(
                    "Turn maintenance on",
                    "PUT",
                    "/api/v1/maintenance",
                    description="Admin only. While on, every other user and anonymous call gets 503 with code maintenance (and a Retry-After header); an admin keeps working. Sign-in is refused for non-admins. Run Turn maintenance off when done.",
                    body="{\n  \"enabled\": true,\n  \"reason\": \"Restoring a backup\"\n}",
                ),
                req(
                    "Turn maintenance off",
                    "PUT",
                    "/api/v1/maintenance",
                    description="Admin only. Idempotent. The command line does the same without the API: python -m mosaicwave.maintenance off.",
                    body="{\n  \"enabled\": false\n}",
                ),
            ],
        ),
    ],
}


def env(name: str, values: list[tuple[str, str, str]]) -> dict:
    return {
        "name": name,
        "_postman_variable_scope": "environment",
        "values": [
            {"key": k, "value": v, "type": t, "enabled": True} for k, v, t in values
        ],
    }


local = env(
    "mosaicWave local",
    [
        ("baseUrl", "http://127.0.0.1:8000", "default"),
        ("username", "admin", "default"),
        ("password", "", "secret"),
        ("sourcePath", r"C:\Photos", "default"),
        ("takeoutPath", r"C:\Takeout", "default"),
        ("destPath", r"C:\Photos", "default"),
        ("fsPath", "", "default"),
        ("dataDir", r"D:\mosaicWave-data", "default"),
        ("memberUsername", "pat", "default"),
        ("memberPassword", "memberpass", "secret"),
        ("memberDisplayName", "Pat", "default"),
    ],
)

qnap = env(
    "mosaicWave QNAP",
    [
        ("baseUrl", "http://fileserver:8090", "default"),
        ("username", "", "default"),
        ("password", "", "secret"),
        ("sourcePath", "/share/CACHEDEV1_DATA/Public/Photos", "default"),
        ("takeoutPath", "/share/CACHEDEV1_DATA/Public/Takeout", "default"),
        ("destPath", "/share/CACHEDEV1_DATA/Public/Photos", "default"),
        ("fsPath", "/share", "default"),
        ("dataDir", "/share/CACHEDEV1_DATA/.mosaicWave", "default"),
        ("memberUsername", "", "default"),
        ("memberPassword", "", "secret"),
        ("memberDisplayName", "", "default"),
    ],
)


def dump(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    dump(OUT / "mosaicwave.postman_collection.json", collection)
    dump(OUT / "mosaicwave.local.postman_environment.json", local)
    dump(OUT / "mosaicwave.qnap.postman_environment.json", qnap)
    print("wrote", OUT)
