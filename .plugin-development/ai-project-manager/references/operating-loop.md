# Operating loop

## Morning portfolio run

1. Observe registered projects.
2. Compare with the prior snapshot.
3. Advance ready, safe AI work.
4. Produce one prioritized human digest.

## Active follow-up run

1. Check in-progress AI work.
2. Verify completed outputs.
3. Unlock dependents.
4. Surface only new blockers.

## End-of-day run

1. Summarize verified progress.
2. List human items due next.
3. Escalate only material overdue blockers.

Recommended scheduled prompt:

```text
Use $project-orchestrator. Read the AI Project Manager ledger and registered projects. Observe only meaningful changes, advance authorized reversible AI work, verify outcomes, and update the ledger. Return nothing unless progress occurred or a human decision/action is required. Combine human items into one short prioritized digest.
```

For local projects, keep the computer and desktop app running. Prefer isolated worktrees for unattended code changes.

The scheduled run should invoke:

```text
node PLUGIN_ROOT/scripts/pm-autopilot.mjs
```

Use `--project PROJECT_ID` for a scoped run. Use `--apply-proposal PROPOSAL_PATH` only after a human has approved that exact generated proposal; stale source hashes prevent application. External ticket publication consumes `ticket-outbox.json` and remains an explicitly approved adapter action.
