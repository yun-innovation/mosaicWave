# Progress

| File | Role |
| --- | --- |
| [status.md](status.md) | Current milestone and next step (~one screen) |
| [progress-todo.md](progress-todo.md) | Open checklists — tick when a deliverable is done |
| [progress-archive.md](progress-archive.md) | Dated milestones and decisions |
| [`.cursor/rules/mosaicwave-status.mdc`](../.cursor/rules/mosaicwave-status.mdc) | Always-on agent snapshot (same facts as status.md) |

Session wrap-up: user says **`mosaic done`** — see [index.md](index.md) § mosaic done. That ticks work items; it does **not** mark a milestone complete.

Milestone complete: user says **`milestone N done`** — then write **Milestone N — done** in [status.md](status.md) and the always-on rule, and archive a milestone-complete row. Do not infer this from wrap-up, tests passing, or “import tested”.
