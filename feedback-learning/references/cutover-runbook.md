# Feedback Learning direct-hook cutover

This runbook changes no live state until the final `--apply` command. Replace paths only
when Codex is installed elsewhere.

```powershell
$SKILL_DIR = "C:\Users\masa\.codex\skills\feedback-learning"
$HOOKS = "C:\Users\masa\.codex\hooks.json"
```

## 1. Inspect current state

```powershell
python -X utf8 "$SKILL_DIR\scripts\configure_hook.py" status --config "$HOOKS"
python -X utf8 "$SKILL_DIR\scripts\configure_hook.py" cutover --config "$HOOKS"
```

The second command is a dry run. Require `cutover.ready=true`. Confirm the plan adds the
feedback direct Hook, preserves or derives the AI Project Manager direct
`capture_prompt.py` Hook, preserves unrelated Hook groups, and removes
`user_prompt_dispatcher.py` only after both direct Hooks exist in the candidate.

## 2. Apply backup → migrate → drain → atomic cutover

```powershell
python -X utf8 "C:\Users\masa\.codex\skills\feedback-learning\scripts\configure_hook.py" cutover --config "C:\Users\masa\.codex\hooks.json" --apply
```

The apply command performs this order:

1. if the existing database is older than the current schema, make a consistent SQLite online backup;
2. privacy-repair and integrity-check the retained backup;
3. migrate the live database with secure-delete and two WAL truncation checkpoints;
4. explicitly drain authenticated, body-free spool envelopes under one nonblocking lock;
5. atomically replace `hooks.json`.

If backup, migration, or drain initialization fails, the Hook configuration is not
written. The Hook itself never creates a key or database.

## 3. Verify

```powershell
python -X utf8 "$SKILL_DIR\scripts\configure_hook.py" status --config "$HOOKS"
python -X utf8 "$SKILL_DIR\scripts\feedback_cli.py" status
python -X utf8 "$SKILL_DIR\scripts\feedback_cli.py" drain
```

Require:

- `installed=true`;
- `pm_direct_installed=true`;
- `dispatcher_installed=false`;
- `schema_version="4"` and `privacy_repair_version="3"`;
- `integrity="ok"`;
- a bounded or zero `pending_spool` count.

The retained database backup is under
`$CODEX_HOME\feedback-learning\backups\feedback-pre-live-migration-v4-*.sqlite3`.
It is a repaired v4 backup: legacy Hook templates are blank, while manual summaries and
the remaining ledger are preserved. Keep it local and access-controlled.
