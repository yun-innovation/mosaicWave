# mosaicWave — documentation index

Planning and architecture for **mosaicWave**: a **multi-platform** photo/video library (QNAP QPKG, Windows, Linux) with a **Python API**, a **Next.js static** web client, and the same API for a future **mobile** client.

Human summary: [README.md](README.md).

---

## For AI Agents

> **Mandatory at session start.** Every new session or task: **read this file** (For AI Agents + Planning TOC) **before** opening source. Then use `.cursor/rules/mosaicwave-status.mdc` for Now / Next / constraints. Docs before code.

### User commands (shortcuts)

| User says | Agent does |
| --- | --- |
| *(nothing)* / start a task | **Read this file first**; then status rule; then task doc; then code |
| **`mosaic`** | Same — confirm one sentence (milestone, next step, target doc) after reading this file |
| **`mosaic status`** | Read this file if not yet read this session; then [status.md](status.md) only to verify the rule |
| **`mosaic index`** | Re-skim **Planning** below (already required at session start) |
| **`mosaic done`** | Session wrap-up — run [§ mosaic done](#mosaic-done--session-wrap-up); **no cold-read** of roadmap/architecture/archive. **Does not** mark a milestone complete |
| **`milestone N done`** | Only then write **Milestone N — done** in status + the always-on rule, archive a milestone-complete row, and move **Next** to milestone N+1 |
| **`read index.md`** | Treat as **`mosaic index`** |

> **Mandatory onboarding — run automatically** when the user says **`mosaic`**, **`mosaic status`**, **`mosaic index`**, or work is clearly mosaicWave: read this file, then the status rule.

### Before starting work

Follow this order on every new session or task:

1. **Read this index** — For AI Agents + Planning TOC (do not skip).
2. **Current status** — if `.cursor/rules/mosaicwave-status.mdc` is loaded, use it and **skip** [status.md](status.md) unless verifying freshness. Otherwise read [status.md](status.md).
3. **Acknowledge in one sentence** — milestone, next step, and which doc you will open for this task. **Do not** restate roadmap/architecture content or wait for approval unless the user asked you to.
4. **Read task-specific docs** — use the status “Docs on demand” / “Read on demand” table or **Planning** below. Prefer documentation over source code when analyzing a task. Product / path names: [naming.md](naming.md).
5. **Only then inspect source code** — after docs give you context, intent, and constraints. Always-on rule: `.cursor/rules/docs-before-code.mdc`.

Root planning docs (open only when the task needs them):

- [Roadmap](roadmap.md) (ordered milestones) / [Architecture](architecture.md) — next work or stack/API/DB
- [Process](process.md) — standalone Windows → QPKG, git, API-first
- [System overview](system-overview.md) — product intent
- [Takeout](takeout.md) — Google Takeout import / sample DB
- [Takeout dump](takeout-dump.md) — zip splits, oversized files, metadata.json, Check folder
- [Workflow](workflow.md) — job table, audit of Takeout import and later tasks
- [Schema](schema.md) — server SQLite tables, `library.db`, offline replica, sync
- [FileStore](filestore.md) — originals I/O, virtual paths, LocalFileStore (v1 done)
- [FileStore later](filestore-later.md) — BYO Drive/OneDrive; **not a milestone**
- [Storage](storage.md) — FileStore / BlobStore requirements, BlobStore, multi-platform I/O
- [UNC / smb://](smb-paths.md) — Settings/Browse `smb://` and UNC → host folder
- [progress-todo.md](progress-todo.md) / [progress-archive.md](progress-archive.md) — checklists or history only when the task requires them

### After program changes

When you change source or **locked decisions**, keep documentation in sync in the **same** task:

1. **Create or update related docs** — behaviour, APIs, packaging, or workflows → matching file in this folder. Create a new doc when none fits.
2. **Update progress** — [status.md](status.md) and [`.cursor/rules/mosaicwave-status.mdc`](../.cursor/rules/mosaicwave-status.mdc) (snapshot, **same date**); [progress-todo.md](progress-todo.md) (checkboxes); [progress-archive.md](progress-archive.md) (dated milestone / decision). See [progress.md](progress.md).
3. **Update root-level docs when scope shifts** — [Roadmap](roadmap.md) or [Architecture](architecture.md) only when milestones or target design change; follow [Process](process.md).
4. **Link from this index** — when adding a new doc, add a row under **Planning** (or a new section).

### mosaic done — session wrap-up

> **Trigger:** user says **`mosaic done`** or **`unity done`**, or asks to wrap up / hand off / end the session after substantive work.
>
> **Also run** (without waiting for the command) when you have finished the requested task and would otherwise stop after **doc or code edits** — tick progress and refresh status so the next session is not stale.

Use when closing a session after substantive work. **Do not** re-read roadmap, architecture, process, or `progress-archive.md` in full. **Do not** run QPKG/NAS tests unless the user asked in this message.

1. **Summarize** — what changed (files + intent) this session, or “no code changes”.
2. **Doc audit** — against [§ After program changes](#after-program-changes): related docs updated, or explicitly “none needed”.
3. **Progress** — tick **work** checkboxes in [progress-todo.md](progress-todo.md) for what this session actually shipped. Refresh [status.md](status.md) / [`.cursor/rules/mosaicwave-status.mdc`](../.cursor/rules/mosaicwave-status.mdc) **Next** only for the next *task*. **Do not** write **Milestone N — done**, do not archive “Milestone N complete”, and do not move the snapshot to milestone N+1. That is only [**`milestone N done`**](#user-commands-shortcuts). `unity done` is this wrap-up, not a milestone gate.
4. **Handoff** — bullet list of open follow-ups for the next session (from conversation, not a full todo re-read).
5. **Report** — short structured reply only; no waiting for approval.

If the session was Q&A only with no edits, steps 2–3 are “N/A” — one-line summary is enough.

---

## Planning

| Document | Description |
| --- | --- |
| [README](README.md) | Short human intro and locked decisions |
| [System overview](system-overview.md) | Product intent, users, in/out of scope |
| [Architecture](architecture.md) | FastAPI, Next export, SQLite, hosts (QPKG + standalone), API |
| [Storage](storage.md) | Virtual FileStore + BlobStore; extra multi-platform requirements |
| [UNC / smb://](smb-paths.md) | Settings/Browse share URLs → host folder (Windows UNC, macOS service mount) |
| [FileStore](filestore.md) | Originals I/O: virtual paths, LocalFileStore, walk/scan (**v1 done**) |
| [FileStore later](filestore-later.md) | BYO Drive/OneDrive/iCloud — **not a milestone** |
| [Process](process.md) | API-first loop, git, QPKG / standalone |
| [Naming](naming.md) | Product string, data dirs, env keys, branches, packages |
| [Roadmap](roadmap.md) | Ordered milestones; old Phase 0–9 plan in [roadmap-archive.md](roadmap-archive.md) |
| [Status](status.md) | **Agent onboarding** — current milestone, next step, constraints |
| [Progress](progress.md) | Hub — status, todo, archive |
| [Progress — To Do](progress-todo.md) | Open items only |
| [Progress — Archive](progress-archive.md) | Milestones and decisions log, plus the old phase checklist |
| [Takeout import](takeout.md) | Google Takeout → SQLite sample library |
| [Takeout dump](takeout-dump.md) | Zip splits, oversized videos, `metadata.json`, inspect |
| [Job workflow](workflow.md) | Task runs in SQLite for audit |
| [Schema](schema.md) | SQLite tables, `library.db`, UUIDs, offline replica, sync |
| [QPKG / standalone hosts](qpkg.md) | `publish.cmd` / `pack.cmd` (`msi` / `qpkg` / `posix`) |
| [Postman collection](../postman/mosaicwave.postman_collection.json) | Importable `/api/v1` collection + environments (not a generated client) |
