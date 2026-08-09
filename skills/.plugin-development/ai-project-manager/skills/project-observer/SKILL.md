---
name: project-observer
description: Collect fresh, read-only evidence about one or more projects and identify meaningful changes, risks, stale plans, blocked work, repository activity, and missing decisions. Use for periodic project checks, portfolio scans, status refreshes, drift detection, and before planning or orchestration decisions.
---

# Project Observer

Observe before interpreting.

1. Resolve explicit observation scope separately from execution scope.
2. Inspect project-owned plans, dated documents, manifests, source/test structure, Git status and recent history, open work items, and prior ledger state.
3. Bound expensive scans and record truncation.
4. Produce facts with source paths and timestamps.
5. Compute changes since the prior observation.
6. Flag stale evidence, malformed plans, blocked dependencies, overdue work, uncommitted planning changes, and missing next actions.
7. Do not modify project files, infer completion without evidence, or invent dates.
8. Return only changes that affect priority, risk, routing, or a required decision.

Use `node ../../scripts/pm-run.mjs [--project PROJECT_ID]` for the bounded deterministic observation, snapshots, plan synchronization, human queue, and ticket outbox. Do not add `--execute` during a read-only observation.
