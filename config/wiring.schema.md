# Wiring Registry Schema

`config/wiring.json` is the single declarative registry of every external
attachment point between this repo and the live machine (junctions, symlinks,
copy-synced files/dirs, unmanaged ledgers, and scheduled tasks). It replaces
the previous hardcoded `DRIFT_MAP` in `scripts/validate_workspace.py`.

## Who writes / reads it

- **Written by**: a human (or an agent under explicit human instruction) when
  wiring is added, removed, or retargeted. Never auto-generated.
- **Read by**: `scripts/validate_workspace.py` (`check_wiring()`, drift checks
  derived from `kind=="copy"` entries) and `scripts/bootstrap_workspace.py`
  (`--check`, `--apply`, `--markdown`).

## Top-level shape

A JSON object with a single key:

```json
{
  "entries": [ { ... }, { ... } ]
}
```

## Entry fields

| Field | Required | Type | Description |
|---|---|---|---|
| `id` | yes | string | Unique slug identifying the entry. |
| `kind` | yes | string enum | One of `junction`, `symlink`, `copy`, `ledger`, `scheduled-task`, `unmanaged`. |
| `repo` | for junction/symlink/copy | string | Path relative to repo root — the canonical, versioned side. |
| `live` | for junction/symlink/copy/ledger | string | Absolute path on the machine (`~` allowed, expanded to `HOME`). |
| `exclude` | no | list of string | For `copy` entries covering a directory: filenames excluded from the copy/drift comparison (e.g. `hooks.json` under `hooks/codex` because it is synced by its own separate entry). For `ledger` entries: filename globs skipped during backup (e.g. a regenerable log store). Every exclusion must state a reason, via the sibling `exclude_reason` field — a free-text string covering the whole `exclude` list. New entries must use `exclude_reason`; do not rely on `description` for exclusion rationale going forward. |
| `exclude_reason` | required when `exclude` is set | string | Why the globs in `exclude` are safe/intended to skip (e.g. "regenerable app logs, not accumulated user data"). |
| `reason` | required for `kind=="unmanaged"` | string | Why this path is intentionally excluded from wiring/drift checks. |
| `name` | for `scheduled-task` | string | The Windows scheduled task name. |
| `interval_minutes` | for `scheduled-task` | number | Trigger interval in minutes. |
| `description` | no | string | Free-text note (e.g. context on why a target is what it is). |

## `kind` semantics and canonical side

- **`junction`** — a Windows directory junction. `live` is the junction point,
  `repo` is the target it must resolve to. Canonical side: `repo` (the repo
  content is the source of truth; the junction is just a view onto it).
- **`symlink`** — a Windows symlink. `live` is the link, `repo` is what it
  points at. Note: a symlink's target may itself be another `live` path from
  a `junction` entry (chained wiring) rather than a repo path directly — when
  that's the case, `repo` should be omitted/empty and a `target_live` field
  used instead, or the entry documents the chain in `description`. In this
  registry the one symlink entry (`~/.claude/skills`) points at another live
  path (`~/.codex/skills`), not directly at the repo, so it uses `live` +
  `target_live` (no `repo` field) to record that accurately.
- **`copy`** — file/dir is duplicated, not linked; the two copies can drift.
  Canonical side: **`repo`** (repo is source of truth; `live` must match it).
  Drift checks run repo → live comparison.
- **`ledger`** — a directory that is NOT in the repo, backed up separately,
  and never synced to/from the repo (e.g. sqlite-backed learning ledgers).
  Only `live` is set; no `repo`. These are checked for existence only, and
  the repo-leak guard (`check_no_ledgers_in_repo`) ensures ledger file types
  never end up inside the repo tree.
- **`scheduled-task`** — a Windows Scheduled Task that is not a filesystem
  path at all; recorded so bootstrap can print (never execute) the
  `schtasks /Create` command to reproduce it.
- **`unmanaged`** — anything intentionally out of scope for wiring/drift
  checks. Requires a `reason` field explaining why. No `repo`/`live`
  enforcement is performed.

## Validation rules (enforced by `check_wiring()`)

- `entries` is a non-empty list; every entry has `id` (unique) and `kind`
  from the enum above.
- `kind=="unmanaged"` requires a non-empty `reason`.
- `kind` in (`junction`, `symlink`, `copy`) requires `live`; `junction` and
  `copy` require `repo`; `symlink` requires either `repo` or `target_live`.
- `kind=="ledger"` requires `live`, forbids `repo`.
- `kind=="scheduled-task"` requires `name` and `interval_minutes`.
- For `junction` and `symlink` entries, the live path must exist and its
  resolved target must equal the declared `repo` (or `target_live`) path —
  this catches a junction/symlink that has been silently retargeted.
