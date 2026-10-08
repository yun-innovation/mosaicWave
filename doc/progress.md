# Progress

| File | Role |
| --- | --- |
| [status.md](status.md) | Current phase and next step (~one screen) |
| [progress-todo.md](progress-todo.md) | Phase checklists — tick when a deliverable is done |
| [progress-archive.md](progress-archive.md) | Dated milestones and decisions |
| [`.cursor/rules/mosaicwave-status.mdc`](../.cursor/rules/mosaicwave-status.mdc) | Always-on agent snapshot (same facts as status.md) |

Session wrap-up: user says **`mosaic done`** (or **`unity done`**) — see [index.md](index.md) § mosaic done. That ticks work items; it does **not** mark a phase complete.

Phase complete: user says **`phase N done`** — then write **Phase N — done** in [status.md](status.md) and the always-on rule, and archive a phase-complete row here. Do not infer this from wrap-up, tests passing, or “import tested”.
