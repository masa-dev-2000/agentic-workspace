# Ledger roadmap contract

Read `~/.codex/ai-project-manager/ledger.json` as the source of truth. Never mark completion from
the visualization.

Required task fields come from AI Project Manager schema version 1. The roadmap also recognizes
these optional additive fields:

| Field | Purpose |
|---|---|
| `parentTaskId` | Explicit node hierarchy |
| `hierarchyPath` | Ordered semantic path such as `["MVP","取込","OCR"]` |
| `phaseId` / `phaseTitle` | Product or delivery phase grouping |
| `workstreamId` / `workstreamTitle` | Cross-functional workstream grouping |
| `milestoneId` | Group tasks into one overview node |
| `milestoneTitle` | Human-facing milestone name |
| `estimate.optimisticMinutes` | Best-case remaining work |
| `estimate.likelyMinutes` | Most likely remaining work |
| `estimate.pessimisticMinutes` | Delayed-case remaining work |
| `startedAt` | Actual start timestamp |
| `progress` | Evidence-backed fraction from 0 to 1 |
| `dependencyEvidence` | Evidence for each declared dependency |
| `execution` | Current execution lease for one agent or human |
| `executions` | Concurrent execution leases when several workers share one task |

An execution lease may contain:

| Field | Purpose |
|---|---|
| `actorType` | `agent`, `human`, or `service` |
| `actorId` | Stable runtime identity |
| `displayName` | Short UI label |
| `role` | Current responsibility, separate from model identity |
| `model` | Optional model identifier; show only in detail |
| `workspacePath` | Worktree or working directory; render relative to the project |
| `startedAt` | Actual start time |
| `heartbeatAt` | Last proof that the worker is still active |
| `state` | `assigned`, `working`, `waiting`, `stale`, or `finished` |

Treat a `working` lease whose heartbeat is older than five minutes as `stale`. Do not infer active
work from the assignee field or an operating-system process alone. Avoid exposing absolute paths in
the viewer. Aggregate workers on milestone, group, and project nodes without duplicating identities.

Each `dependencyEvidence` item should contain:

| Field | Purpose |
|---|---|
| `dependencyId` | Prerequisite task ID |
| `verified` | `true` only after observing the cited source |
| `source` | Repository-relative document, test, code-graph query, issue, or external-state reference |
| `kind` | `code`, `data`, `document`, `approval`, `human`, `service`, `release-gate`, or `declared` |
| `confidence` | `high`, `medium`, or `low` |

Render declared dependencies without evidence as unverified dashed edges. Never present them as
facts merely because they appear in a plan.

Resolve hierarchy in this order:

1. explicit `parentTaskId`;
2. `hierarchyPath`;
3. phase → workstream → milestone fields;
4. evidence-backed project documents;
5. a neutral size batch only as the last fallback.

Do not use task status as the primary hierarchy when any semantic grouping exists. Status is a
filter and aggregate signal, not project structure.

Choose depth by leaf-task count:

| Scale | Leaf tasks | Initial view |
|---|---:|---|
| Small | 1–10 | Tasks |
| Medium | 11–40 | One semantic group level |
| Large | 41–150 | Overview → phase/workstream → milestone/task |
| Very large | 151+ or 5+ projects | Portfolio/project → phase/workstream → milestone/task |

Each view should expose roughly 6–10 immediate children. A single click opens details about the
selected node itself in a collapsible right pane; a double click on a parent enters its immediate
children. The detail pane must not show descendant summaries or child navigation. Collapsing it
must not change the current hierarchy, and navigation preserves a persistent breadcrumb.
Limit the interactive hierarchy to three semantic layers. At layer
three, summarize finer descendants in the node facts or expose them through a separately filtered
view rather than another nested drill-down. Aggregate completion, progress, ETA, blockers,
criticality, and executions from descendants without copying or replacing their leaf evidence.

Without hierarchy evidence, show each task only for small projects. For larger projects, expose
that semantic hierarchy is missing and use clearly labeled neutral batches rather than pretending
that status buckets are phases.

Compute forecasts from remaining dependency paths. Show optimistic, likely, and pessimistic times
plus confidence. Treat missing estimates and blocked/waiting tasks as low confidence. Forecasts are
decision support, never promises.

Treat ledger status and release impact separately:

- `blocked`: show `BLOCKED` only when the task records an actual failure or unmet mandatory
  release condition.
- `waiting-human`: show `確認待ち`; promote to `BLOCKED` only when it lies on a verified critical
  or release path and an unfinished required dependent cannot proceed.
- `waiting-service`: show `外部待ち` under the same promotion rule.
- A wait with no unfinished dependent is non-blocking even when important.

Watch the ledger's parent directory because AI Project Manager writes atomically by renaming a
temporary file. Debounce filesystem notifications and retain periodic mtime polling as fallback.
