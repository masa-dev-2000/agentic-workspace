# Operations Manual — agentic-workspace

Audience: a competent engineer who is **not** the author, who has to run,
recover, or extend this system. Every section below points at a command you
can actually run, rather than restating internal logic. Run commands from the
repo root unless noted otherwise.

## 1. What this is / BLAST RADIUS FIRST

`agentic-workspace` is a provider-neutral personal agentic harness: skills,
Claude Code subagents, hooks, slash commands, and a decision-criteria ledger,
wired into the vendor-specific locations each tool (Claude Code, Codex CLI,
Gemini CLI) actually reads from.

**Read this before editing anything.** Several live locations are not
copies — they are Windows directory junctions or symlinks straight into this
repo:

- `~/.claude/agents` is a junction to `agents/claude/` in this repo.
- `~/.codex/skills` and `~/.agents/skills` are junctions to `skills/`.
- `~/.claude/skills` is a symlink to `~/.codex/skills` (which is itself the
  junction above — a chained link back to `skills/`).

This means **editing `agents/claude/*.md` or anything under `skills/` in this
repo is live in the running harness instantly, with no deploy step, no
restart, no CI gate.** A typo in an agent's frontmatter, or a bad instruction
in a skill body, changes what Claude Code does on your very next tool call.
There is no staging environment for this — `agents/proposed/` exists
precisely so agent-steward can draft changes without this blast radius (see
§3), but once a file lands under `agents/claude/` it is hot.

Everything else (`hooks/claude`, `commands/claude`, `config/CLAUDE.global.md`,
`hooks/codex`) is `kind: copy` in the wiring registry (§2) — a real copy that
can drift from the repo and must be explicitly re-synced
(`bootstrap_workspace.py --apply`, or the one-line `cp` in its `--check`
output). Ledgers (`~/.codex/failure-learning`, etc.) are not synced at all —
they are unmanaged local state, backed up separately (§7).

Validation: `python -X utf8 scripts/validate_workspace.py` (see §2 for what
it checks). It is not a gate on live edits — it only tells you *after the
fact* that something is wrong. `.githooks/pre-push` runs it before every
`git push`, but nothing runs it before a live file is edited in place.

## 2. Wiring map

The wiring registry (`config/wiring.json`, schema in
`config/wiring.schema.md`) is the single declarative source for every
junction/symlink/copy/ledger/scheduled-task attachment point between this
repo and the machine. Do not hand-maintain a second copy of this table
anywhere — regenerate it:

```
python -X utf8 scripts/bootstrap_workspace.py --markdown
```

Actual output (run from the repo root, 2026-08-10):

| id | kind | repo | live |
|---|---|---|---|
| codex-skills-junction | junction | skills | ~/.codex/skills |
| agents-skills-junction | junction | skills | ~/.agents/skills |
| claude-agents-junction | junction | agents/claude | ~/.claude/agents |
| claude-skills-symlink | symlink |  | ~/.claude/skills |
| claude-hooks-copy | copy | hooks/claude | ~/.claude/hooks |
| claude-commands-copy | copy | commands/claude | ~/.claude/commands |
| claude-global-md-copy | copy | config/CLAUDE.global.md | ~/.claude/CLAUDE.md |
| claude-user-root-md-copy | copy | config/CLAUDE.user-root.md | ~/CLAUDE.md |
| codex-hooks-json-copy | copy | hooks/codex/hooks.json | ~/.codex/hooks.json |
| codex-hooks-dir-copy | copy | hooks/codex | ~/.codex/hooks |
| failure-learning-ledger | ledger |  | ~/.codex/failure-learning |
| feedback-learning-ledger | ledger |  | ~/.codex/feedback-learning |
| skill-telemetry-ledger | ledger |  | ~/.codex/skill-telemetry |
| adaptive-orchestrator-ledger | ledger |  | ~/.codex/adaptive-orchestrator |
| self-clone-ledger | ledger |  | ~/.codex/self-clone |
| codex-app-state-ledger | ledger |  | ~/.codex/sqlite |
| codex-skill-telemetry-optimizer-task | scheduled-task |  | Codex Skill Telemetry Optimizer |
| agentic-weekly-health-task | scheduled-task |  | agentic-weekly-health |
| agentic-ledger-backup-task | scheduled-task |  | agentic-ledger-backup |

This table cannot drift from `config/wiring.json` because it's generated from
it — if it looks wrong, the JSON file is wrong, not this doc. For the field
meanings (what `kind` implies about which side is canonical, exclude
patterns, etc.) read `config/wiring.schema.md` directly rather than
duplicating it here.

To check whether the *live machine* actually matches this table:

```
python -X utf8 scripts/bootstrap_workspace.py --check
```

Important caveat verified while writing this doc: `--check` compares live
paths against **this checkout's own path**, not against "whatever checkout
is canonical." Run from a worktree (as this doc was), every junction/copy
reports `WRONG-TARGET` because the live junctions point at
`C:\Users\masa\dev\agentic-workspace` (the main clone), not the worktree.
That is expected and not a real problem — it only means: run `--check` (and
`--apply`) from the machine's canonical clone, not from a throwaway worktree.
Ledger and scheduled-task entries reported `OK` even from the worktree, since
those checks don't depend on repo path.

To validate the registry's own structure and drift without touching the live
filesystem (e.g. in CI, or inside a worktree):

```
python -X utf8 scripts/validate_workspace.py --no-live
```

Actual output:

```
skipped: drift check, wiring live-path/junction/symlink resolution (--no-live)
OK: agents valid, no drift, criteria consistent, wiring valid, no leaked ledgers
```

Plain `python -X utf8 scripts/validate_workspace.py` (no flag) is the normal
mode outside a worktree — it additionally runs the drift check and the
junction/symlink live-resolution check against this machine's real
`~/.claude`, `~/.codex`, etc. Use `--no-live` only when there is no
meaningful "this machine" context to check against (CI, or a worktree whose
junctions necessarily point elsewhere).

### Other platforms

All OS differences (link creation/resolution, scheduler registration/query,
shell resolution, default backup/health paths) live in exactly one place:
`scripts/platform_adapter.py`. No other script branches on `sys.platform`.
On macOS/Linux, `junction` and `symlink` wiring entries both resolve to a
plain `os.symlink` (see `config/wiring.schema.md`'s "kind semantics per
platform"); `bootstrap_workspace.py --check`/`--apply` and
`validate_workspace.py` behave identically to the Windows flow described
above — same OK/MISSING/WRONG-TARGET states, same "repo is canonical" rule —
just backed by `ln -s` instead of `mklink`. `scheduled-task` entries recreate
as a launchd agent on macOS or a systemd --user timer on Linux (crontab-line
fallback if systemd --user is unavailable) instead of a Task Scheduler XML
import; see `config/wiring.schema.md` for the mechanism table.

## 3. The pipeline

Four agents plus two governance loops, defined in `agents/claude/*.md`
(junctioned live per §1 — editing one of these files changes agent behavior
immediately):

- **issue-finder** (`agents/claude/issue-finder.md`) — read-only discovery.
  Scans code/tests/telemetry/ledgers for candidate issues against a caller-
  assigned lens (correctness, security, performance, docs, tooling, …).
  Never writes to the ledger, never fixes anything. Output: a list of
  candidates with evidence, not filed issues.
- **issue-ledger** (`agents/claude/issue-ledger.md`) — sole writer of the
  issue ledger (GitHub Issues via `gh`, since this repo has a GitHub
  remote). Accepts candidates from issue-finder or triage intake (below),
  de-duplicates, scores against `criteria/CRITERIA.md`, owns status
  transitions (open → ready → in-progress → verify → closed).
- **issue-orchestrator** (`agents/claude/issue-orchestrator.md`) — consumes
  one `ready` issue end to end: plan → implement → verify → adversarial
  review → commit on a branch → PR prepared. Never merges, never touches
  issue priority/criteria.
- **adversarial-reviewer** (`agents/claude/adversarial-reviewer.md`) —
  read-only; reviews the diff looking for reasons to reject it. Called by
  issue-orchestrator before it reports done.

Governance loops (not part of the per-issue pipeline; run periodically or on
demand):

- **criteria-steward** (`agents/claude/criteria-steward.md`) — defines,
  accumulates evidence for, and revises the decision axes in `criteria/`.
  Drafts only; **never sets a criterion to `status: active` itself.**
- **agent-steward** (`agents/claude/agent-steward.md`) — audits the agent
  roster, drafts new agents to `agents/proposed/` on a real recurring gap,
  proposes retirement on evidence of disuse. **Never moves a file into
  `agents/claude/` without explicit human approval** (and the move itself is
  the live-blast-radius event from §1).

### Human gates (explicit, not automatic)

1. **Criteria activation** — a criterion file only takes effect
   (`status: active`, enforced by `check_criteria()`) once it carries a
   structured approval line: `Approval: actor=<provider>:<id>
   channel=<channel> date=<YYYY-MM-DD> ref="<...>"` (format and regex in
   `criteria/SCHEMA.md`). criteria-steward drafts `proposed`; a human writes
   or dictates the approval line before it can go `active`.
2. **`agents/proposed/` → `agents/claude/`** — agent-steward drafts a new
   agent under `agents/proposed/`, which is not loaded by any harness. Moving
   it into `agents/claude/` (making it live per §1) happens only "when the
   human explicitly approves a named proposal" (agent-steward's own
   contract, step 4).
3. **PR merge** — issue-orchestrator prepares a PR but never merges it, and
   never merges to the default branch or deploys. That belongs to a human or
   the separate dev-flow merge session (`/kio-devmerge`, `/kio-mainmerge`
   equivalents for this repo's own flow are out of scope for this repo — see
   `weekly-ops.md`'s explicit "does not merge" line in §4).

### Intake path

Public-facing intake is GitHub issue forms
(`.github/ISSUE_TEMPLATE/bug.yml`, `.github/ISSUE_TEMPLATE/idea.yml`;
`config.yml` sets `blank_issues_enabled: false` so free-form issues are not
allowed). Both forms require a concrete `Evidence` field (file:line, a
command plus its observed output, or a log excerpt) and explicitly warn
against pasting secrets or ledger contents (this repo is public — see §7).
Every form-filed issue is labelled `status:needs-triage` and
`intake:external` on creation.

Triage is Step 0 of `/weekly-ops` (§4): enumerate
`status:needs-triage` issues via
`gh issue list --state open --label "status:needs-triage" --json number,title,labels,body --limit 100`,
then hand them to issue-ledger, which treats each exactly like an
issue-finder candidate — not pre-approved — validating evidence, de-duping,
scoring against criteria, and either relabeling (lens + priority) or closing
with a rejection reason (per `issue-ledger.md`'s Triage section).

## 4. Cadence

| Cadence | What | Mechanism | Registered task name |
|---|---|---|---|
| Daily | Ledger backup | Scheduled Task (unattended) | `agentic-ledger-backup` |
| Weekly (deterministic) | Health probe (hook defense probe, wiring, backup freshness, scheduled-task status) | Scheduled Task (unattended) | `agentic-weekly-health` |
| Weekly (human-initiated) | `/weekly-ops` — the LLM-judgment half that can't be automated: intake triage, criteria-coverage sweep, rotating-lens issue-finder sweep, candidate filing | Slash command, run by a human | n/a (not a scheduled task) |
| Quarterly | Agent roster audit | Folded into `/weekly-ops` Step 5, gated `week % 13 == 1` | n/a (same command, conditional step) |
| Every 15 min | Skill telemetry optimizer | Scheduled Task (pre-existing, not part of this repo's phases) | `Codex Skill Telemetry Optimizer` |

`/weekly-ops` (`commands/claude/weekly-ops.md`) explicitly does **not**:
merge PRs, activate criteria, hire/activate agents, or run `health_check.py`
itself (that's the Scheduled Task's job). Its steps, in fixed order:

0. Drain intake (`status:needs-triage` → issue-ledger, per §3).
1. Display the latest health report verbatim
   (`%USERPROFILE%\.claude\health\latest.md`); if it doesn't exist, say so
   explicitly rather than guessing.
2. Criteria-coverage sweep: enumerate open issues labelled
   `needs-criterion` and hand each to criteria-steward for axis drafting
   (drafts only — activation is still gated per §3).
3. issue-finder sweep with a lens chosen deterministically by ISO week
   number (`week % 4`: 0=validation, 1=drift, 2=docs, 3=tooling — get the
   week with `python -c "import datetime; print(datetime.date.today().isocalendar().week)"`).
4. Candidates from Step 3 go straight to issue-ledger.
5. Quarterly-only agent-steward roster audit, gated `week % 13 == 1`
   (documented reason for not running weekly: a small 6-agent roster
   audited every week produces near-zero new signal and just adds noise to
   the `validator-signal-hygiene` warn channel — do not "fix" this to
   run weekly).

### Other platforms

The Daily and Weekly rows above are unattended OS-scheduler jobs; the
mechanism is per-platform (Task Scheduler / launchd / systemd --user, see
`config/wiring.schema.md`) but the cadence, the scripts invoked
(`backup_ledgers.py`, `health_check.py`), and their pass/fail contract are
identical everywhere — `health_check.py`'s SCHEDULED TASKS section queries
whichever mechanism is live via `platform_adapter.query_task()` and reports
`PASS`/`FAIL`/`UNVERIFIED` the same way regardless of OS. `/weekly-ops` and
the LLM-judgment steps are unaffected by platform entirely (they invoke `gh`
and Claude Code agents, not OS schedulers).

## 5. Recovery runbooks

Each ends in a command whose actual output is shown; use it to confirm the
runbook worked.

### (a) New machine

This runbook is the same on Windows, macOS, and Linux — the only things that
differ are the mechanism `platform_adapter.py` picks (see §2 "Other
platforms") and the paths it defaults to (`%USERPROFILE%` on Windows,
`$HOME` on macOS/Linux via `pathlib.Path.home()`).

1. `git clone <repo-url>` to the intended location (the wiring registry
   assumes a specific path per entry's `live` side is relative to `~`, but
   the `repo` side is wherever you cloned to).
2. `python -X utf8 scripts/bootstrap_workspace.py --apply` — creates missing
   junctions/symlinks (`ln -s` on macOS/Linux, `mklink` on Windows — both via
   `platform_adapter.create_link()`), syncs `copy` entries repo→live. It
   never touches `~/.claude/settings.json` directly; instead it diffs
   against `config/settings.claude.reference.json` and prints a
   `SETTINGS-DIFF` block for you to paste in by hand (confirmed by reading
   `bootstrap_workspace.py:186-200`).
3. Paste the printed settings fragment into `~/.claude/settings.json`
   yourself.
4. Register the scheduled tasks. **This step is unverified in this
   session — no scheduler mutation was run on any platform** (registering
   tasks is a machine-mutating action outside this doc's scope). The exact
   reproduction command for each is printed by `bootstrap_workspace.py
   --check` for every `scheduled-task` entry, via
   `platform_adapter.describe_register_command()`:
   - **Windows**: points at `scripts/register_tasks.py`, which imports the
     matching XML under `scripts/scheduled-tasks/*.xml` — that is the
     actually-supported path, not hand-building a `schtasks` string.
   - **macOS**: `launchctl load -w ~/Library/LaunchAgents/com.agentic-workspace.<name>.plist`
     — the plist itself is generated at registration time by
     `platform_adapter.register_task()`, never checked into the repo.
   - **Linux**: `systemctl --user enable --now <name>.timer` if systemd
     --user is available; otherwise the printed command is a `crontab -e`
     line instead — stated explicitly, never a silent no-op.
5. Verify: `python -X utf8 scripts/health_check.py` — writes
   `<health-dir>/latest.md` and appends to `history.jsonl`
   (`%USERPROFILE%\.claude\health` on Windows, `~/.claude/health` on
   macOS/Linux, both via `platform_adapter.health_dir()`), printing
   `HEALTH OK` or `HEALTH FAIL (n)` plus a per-section breakdown.

### (b) Ledger corruption

1. Identify the most recent good backup run under
   `%USERPROFILE%\backups\agentic-workspace-ledgers\<run>\` (timestamped
   `YYYYMMDDTHHMMSSZ` directories; each has a `manifest.json` recording
   per-file status/sha256).
2. Copy the relevant ledger's files from `<run>\<ledger-id>\` back over the
   live path (e.g. `~/.codex/failure-learning`). The backup tool uses
   `sqlite3 .backup()` for `.sqlite3`/`.db` files, so what's in the backup
   is a consistent snapshot, not a raw file copy of a possibly-mid-write
   database.
3. Verify the restored database is not corrupt:
   ```
   python -X utf8 -c "import sqlite3; print(sqlite3.connect(r'<path>').execute('PRAGMA integrity_check').fetchall())"
   ```
   Confirmed against a real backed-up file in this session:
   ```
   [('ok',)]
   ```
   Any result other than `[('ok',)]` means the restored copy is still bad —
   go one run further back.

### (c) Drift FAIL

`validate_workspace.py` (live mode) and `bootstrap_workspace.py --check`
both report drift for `kind: copy` entries as a bare "these two paths
differ," with no built-in opinion on which side to trust. **Resolution
rule, which this doc is establishing to close open issue #6:** for every
`kind: copy` entry, **the repo is canonical.** The `live` side must be made
to match `repo`, never the reverse — this matches
`config/wiring.schema.md`'s own statement under `copy`: "Canonical side:
`repo` (repo is source of truth; `live` must match it). Drift checks run
repo → live comparison." Fix with the exact command
`bootstrap_workspace.py --check` prints for that entry, e.g.:
```
cp -r "<repo path>"/* "<live path>"/
```
or run `bootstrap_workspace.py --apply`, which performs the same copy for
every entry currently not `OK`. For `junction`/`symlink` entries, canonical
side is also `repo` (the junction/symlink is "just a view onto it," per the
same schema doc) — re-create the link rather than editing through the live
path if it was ever retargeted.

Verify: rerun `python -X utf8 scripts/bootstrap_workspace.py --check` from
the canonical clone (not a worktree — see the §2 caveat) and confirm every
`copy`/`junction`/`symlink` entry reports `OK`.

### (d) Hook probe FAIL

`health_check.py`'s DEFENSE PROBE section pipes synthetic input into every
`hooks/claude/*.sh` script and asserts both the block and allow directions.
Before assuming a hook itself is broken, check the parser dependency first:
`hooks/claude/validate-command.sh` reads its stdin via `jq` if present, else
falls back to `python3`, else `python`, and if **none** of the three are on
PATH it hits `exit 0 # パーサーが使えない場合は通過させる` (line 15) — i.e.
it fails open and lets everything through, including commands it should
block. A FAIL from the "allow" case is a real hook bug; a FAIL (or
unexpectedly-passing "block" case) after a PATH change should first be
checked against:
```
command -v jq; command -v python3; command -v python
```
Confirmed on this machine: `jq`, `python3`, and `python` (3.11.15) are all
present, so this hook was not degraded in this session — but a fresh
machine or a stripped-down CI image is exactly where this silently fails
open. Verify the fix (PATH restored, or the hook itself repaired) with:
```
python -X utf8 scripts/health_check.py
```
and confirm the `DEFENSE PROBE` section reports `OK` with all four
`PASS:` lines (two per hook × two hooks that assert block/allow;
`audit-config.sh` only asserts it runs and exits 0, since it's a logging-only
hook).

### (e) Scheduled task stopped

`health_check.py`'s SCHEDULED TASKS section runs
`schtasks /Query /TN <name> /FO LIST /V` for every `kind: scheduled-task`
wiring entry, parses `Last Result` and `Last Run Time`, and fails if the
result is nonzero or the last run is older than 2× the declared
`interval_minutes`. If `schtasks` itself can't be reached (missing binary,
permission error) the section reports `UNVERIFIED` for that task rather than
`FAIL` — those are treated distinctly in the report but do not fail the
overall exit code.

1. Open Task Scheduler (`taskschd.msc`) and locate the task by the registered
   name (`agentic-ledger-backup` or `agentic-weekly-health` — both under the
   task URI root `\`, per their XML's `<URI>`).
2. If it's disabled or missing, re-import from
   `scripts/scheduled-tasks/agentic-ledger-backup.xml` or
   `agentic-weekly-health.xml` via Task Scheduler's "Import Task…" action —
   this reproduces the exact trigger, principal (`LeastPrivilege`,
   `InteractiveToken`), and `<Actions>` command (`python -X utf8
   "C:\Users\masa\dev\agentic-workspace\scripts\<script>.py"` with that repo
   path as `<WorkingDirectory>`) that were captured in the XML.
3. **Registering/re-registering the task was not executed in this session —
   this step is unverified.** Only the XML contents and the read-only
   `schtasks /Query` reporting path in `health_check.py` were verified.
4. Verify: `python -X utf8 scripts/health_check.py` and check the
   `SCHEDULED TASKS` section for `PASS: <name>: Last Result=0, Last Run
   Time=<...>` for the affected task (after it has actually run once).

## 6. Deferred, with reasons

Do not re-litigate these without new evidence — they were considered and
explicitly rejected for this repo's current state, per README.md and the
agent contracts:

- **Branch protection** — not enabled. This is a single-owner repo (`git
  user: m-takehara555` / `masa-dev-2000`); the owner either bypasses
  protection or is taxed by it, either way adding no real control. The
  pre-push hook (`.githooks/pre-push`) plus CI (`.github/workflows/
  validate.yml`) are the actual enforcement mechanism instead.
- **RBAC / roles-permissions matrix for criteria approval** — explicitly
  deferred in `criteria/SCHEMA.md`'s "Deferred: roles and permissions"
  section until a second human identity exists in this workspace. The
  `actor=<provider>:<id>` namespacing in the structured approval line is the
  only forward-compatible groundwork laid now.
- **Headless LLM cadence** (running the pipeline agents unattended, not just
  the two deterministic Scheduled Tasks) — deferred because it rests on two
  unverified things: (1) whether the CLI flags needed to run an agent
  headlessly behave as assumed, and (2) whether Claude Code hooks fire at
  all in a headless/non-interactive session. Until both are actually tested,
  only `health_check.py` and `backup_ledgers.py` (plain scripts, no LLM
  invocation) are scheduled unattended; `/weekly-ops` and the four pipeline
  agents remain human-initiated.

## 7. Privacy boundary

The GitHub repo backing this workspace is **PUBLIC**. Ledgers — sqlite
databases and key files holding accumulated user data — must never enter it.
Two independent mechanical guards enforce this, not just convention:

1. **`check_no_ledgers_in_repo()`** in `scripts/validate_workspace.py`
   (lines 242-245) globs the entire repo tree for `**/*.sqlite3*`,
   `**/*.db*`, and `**/*.key` and fails the validator on any hit — this runs
   in every `validate_workspace.py` invocation, including the `--no-live`
   CI path in `.github/workflows/validate.yml`, so a leaked ledger file
   fails CI on the PR that introduced it, not just at push time.
2. **The destination assertion in `backup_ledgers.py`**
   (`assert_destination_safe`, lines 136-152) hard-checks that the backup
   destination is neither inside the repo root nor inside *any* git work
   tree (walking up from the destination looking for a real `.git` marker)
   before writing anything, and calls `sys.exit(3)` rather than using Python
   `assert` specifically so it cannot be silently stripped by `python -O`.
   Verified in this session:
   ```
   Destination: C:\Users\masa\backups\agentic-workspace-ledgers\20260809T232302Z
   Free space at C:\Users\masa\backups\agentic-workspace-ledgers: 290.40 GiB
   CHECK OK: 6 declared ledger(s), 5908 file(s) would be backed up (91.07 MiB), ...
   ```
   — the destination is under `%USERPROFILE%\backups\...`, entirely outside
   any repo.

**What is backed up and what is excluded, and why** (from
`config/wiring.json`'s `kind: ledger` entries):

- `~/.codex/self-clone` (judgment axes, episodes, knowledge observations,
  evaluations, audit log, plus `identity.key.dpapi`) — **backed up in full**,
  no excludes. This is accumulated, non-regenerable user data.
- `~/.codex/sqlite` (Codex CLI's own app state: state/memories/goals/
  automation dbs) — **backed up**, except `logs_2.sqlite*`, which is
  excluded because it's regenerable app-log data (~78MB), not accumulated
  user state.
- `~/.codex/failure-learning`, `~/.codex/feedback-learning`,
  `~/.codex/adaptive-orchestrator` — backed up in full.
- `~/.codex/skill-telemetry` — backed up **except**
  `optimization/rejected/**` and `optimization/legacy-invalid/**`: those are
  43k+ already-adjudicated proposals and superseded records — outcomes of
  decisions already recorded in the DB, not restorable state, and their file
  count made a full backup fail to complete within a usable window
  (documented via `exclude_reason` in `config/wiring.json`, per the schema's
  requirement that every exclusion state a reason).
- Backup tool also skips, everywhere: `*.pre-*` / `*.before-reconcile-*` /
  `telemetry.pre-*` dead snapshots (already-dead copies the ledgers made of
  themselves), and `-wal`/`-shm` sqlite companion files (transient, not
  meaningful standalone).
- **Undeclared-ledger guard**: `discover_undeclared_ledgers()` globs
  `~/.codex/*/` for any directory containing `*.sqlite3`/`*.db` not covered
  by a declared `kind: ledger` entry, and aborts the whole backup run
  (`sys.exit(3)`) rather than silently skipping it — a new ledger must be
  added to `config/wiring.json` by a human before it can be backed up at
  all (enforces the `drift-coverage-completeness` criterion).

Retention: `prune_old_runs()` keeps the last 7 runs plus the first run of
each calendar month, deleting the rest — so `%USERPROFILE%\backups\
agentic-workspace-ledgers\` does not grow unbounded.
