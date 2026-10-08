# mosaicWave — documentation index

Planning and architecture for **mosaicWave**: a **multi-platform** photo/video library (QNAP QPKG, Windows, Linux) with a **Python API**, a **Next.js static** web client, and the same API for a future **mobile** client.

Human summary: [README.md](README.md).

---

## For AI Agents

> **Default — no command needed.** `.cursor/rules/mosaicwave-status.mdc` is always loaded; phase, next step, and constraints are already in context. **Do not read this file on session start** unless the user asks or you need the TOC / wrap-up triggers below.
>
> **Ignore** [index.md.sample](index.md.sample) — that is a copy from another project (ARRehab.Unity), not mosaicWave.

### User commands (shortcuts)

| User says | Agent does |
| --- | --- |
| *(nothing)* / start a task | Use `mosaicwave-status.mdc` only; open task-specific docs from its “Docs on demand” table |
| **`mosaic`** | Same as default — confirm one sentence (phase, next step, target doc); **no file reads** |
| **`mosaic status`** | Read [status.md](status.md) only — verify the rule is current |
| **`mosaic index`** | Skim **Planning** below for doc links; skip “For AI Agents” unless you need a command |
| **`mosaic done`** | Session wrap-up — run [§ mosaic done](#mosaic-done--session-wrap-up); **no cold-read** of roadmap/architecture/archive. **Does not** mark a phase complete |
| **`phase N done`** | Only then write **Phase N — done** in status + the always-on rule, archive a phase-complete milestone, and move **Next** to phase N+1 |
| **`read index.md`** | Treat as **`mosaic index`** — not a full-file read |

> **Mandatory onboarding — run automatically** when the user says **`mosaic`**, **`mosaic status`**, **`mosaic index`**, or work is clearly mosaicWave and the status rule is missing from context.

### Before starting work

Follow this order on every new session or task:

1. **Current status** — if `.cursor/rules/mosaicwave-status.mdc` is loaded, use it and **skip** [status.md](status.md) unless verifying freshness. Otherwise read [status.md](status.md). Skim this index TOC only if you need to locate a nested doc.
2. **Read root planning docs on demand** — not on every session:
   - [Roadmap](roadmap.md) (being rebuilt) / [Architecture](architecture.md) — open items or stack/API/DB
   - [Process](process.md) — standalone Windows → QPKG, git, API-first
   - [System overview](system-overview.md) — product intent
   - [Takeout](takeout.md) — Google Takeout import / sample DB
   - [Takeout dump](takeout-dump.md) — zip splits, oversized files, metadata.json, Check folder
   - [Workflow](workflow.md) — job table, audit of Takeout import and later tasks
   - [Schema](schema.md) — server SQLite tables, `library.db`, offline replica, sync
   - [FileStore](filestore.md) — originals I/O, virtual paths, LocalFileStore (v1 done)
   - [FileStore later](filestore-later.md) — BYO Drive/OneDrive; **not a phase**
   - [Storage](storage.md) — FileStore / BlobStore requirements, BlobStore, multi-platform I/O
   - [UNC / smb://](smb-paths.md) — Settings/Browse `smb://` and UNC → host folder
   - [progress-todo.md](progress-todo.md) / [progress-archive.md](progress-archive.md) — checklists or history only when the task requires them
3. **Acknowledge in one sentence** — phase, next step, and which doc you will open for this task. **Do not** restate roadmap/architecture content or wait for approval unless the user asked you to.
4. **Read task-specific docs** — use the [status.md](status.md) “Read on demand” table or **Planning** below. Prefer documentation over source code when analyzing a task.
5. **Only then inspect source code** — after docs give you context, intent, and constraints.

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
3. **Progress** — tick **work** checkboxes in [progress-todo.md](progress-todo.md) for what this session actually shipped. Refresh [status.md](status.md) / [`.cursor/rules/mosaicwave-status.mdc`](../.cursor/rules/mosaicwave-status.mdc) **Next** only for the next *task* (e.g. a remaining Phase 2 item). **Do not** write **Phase N — done**, do not archive “Phase N complete”, and do not move the snapshot to phase N+1. That is only [**`phase N done`**](#user-commands-shortcuts). `unity done` is this wrap-up, not a phase gate.
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
| [FileStore later](filestore-later.md) | BYO Drive/OneDrive/iCloud — **not a phase** |
| [Process](process.md) | API-first loop, git, QPKG / standalone |
| [Roadmap](roadmap.md) | Being rebuilt; old Phase 0–9 plan in [roadmap-archive.md](roadmap-archive.md) |
| [Status](status.md) | **Agent onboarding** — current phase, next step, constraints |
| [Progress](progress.md) | Hub — status, todo, archive |
| [Progress — To Do](progress-todo.md) | Open items only |
| [Progress — Archive](progress-archive.md) | Milestones and decisions log, plus the old phase checklist |
| [Takeout import](takeout.md) | Google Takeout → SQLite sample library |
| [Takeout dump](takeout-dump.md) | Zip splits, oversized videos, `metadata.json`, inspect |
| [Job workflow](workflow.md) | Task runs in SQLite for audit |
| [Schema](schema.md) | SQLite tables, `library.db`, UUIDs, offline replica, sync |
| [QPKG / standalone hosts](qpkg.md) | `publish.cmd` / `pack.cmd` (`msi` / `qpkg` / `posix`) |
| [Postman collection](../postman/mosaicwave.postman_collection.json) | Importable `/api/v1` collection + environments (not a generated client) |
