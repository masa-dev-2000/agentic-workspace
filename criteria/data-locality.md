---
id: data-locality
statement: Project-specific data is written inside the project it belongs to, and only cross-project assets live in the shared workspace, which is public.
status: proposed
version: 1
---

## Rationale

An agent contract that says "write to `X/`" is read as an absolute path, but the agent
runs against many projects. Whatever `X` names then collects data from all of them.
When `X` sits in a public repository, that is a disclosure path, not a filing decision.

The split that avoids it is by *ownership*, not by convenience:

- **Cross-project assets** (agent definitions, decision criteria, skills, the wiring
  registry) genuinely belong in one shared place. Copying them per project would create
  drift, and they describe how work is done everywhere.
- **Project-specific data** (issues, work logs, evidence, generated artifacts) belongs
  with the project. It is only meaningful in that context, and pooling it loses the
  project identity needed to answer "is this already filed?".

Stating the split as a criterion — rather than deciding case by case — is what makes it
survive a contract being written while only one project is in view.

## Scope

Applies to every agent and skill that writes files: the target directory for issues,
logs, evidence, generated documents, and any ledger. Applies when writing to
`agentic-workspace` specifically, because that repository is public.

Does not apply to reading. Reading across projects is how horizontal deployment works.

## How to apply

Before writing a path into a contract, ask: **if this agent runs against a different
project tomorrow, does this path still name the right place?**

- Path names shared, cross-project assets → the shared workspace is correct.
- Path names the target project's own material → derive it from the target, never
  hardcode the workspace.

## Counterexamples / Boundaries

- The mistake-prevention scorecard (`ミス防止ルール星取表.xlsx`) is deliberately shared
  across all projects: horizontal deployment requires comparing projects in one table.
  Shared-by-design is fine when the data's purpose is comparison across projects.
- Decision criteria and agent definitions are shared for the same reason. This criterion
  does not push everything into project folders — it pushes *project-specific* data there.
- A private shared location (outside a public repo) is an acceptable middle ground when
  cross-project analysis genuinely needs pooling; the public/private distinction matters
  more than the shared/local one.

## Evidence

- 2026-08-11: `issue-ledger` was contracted to write markdown issues to `issues/` in
  agentic-workspace whenever the target project had no GitHub remote. Every project
  without a remote would have had its issue text — customer names, business detail —
  published, and pooled with no project identifier. The backend had never been used, so
  nothing leaked. Found by asking how the agent behaves across multiple repositories.
- The same single-project assumption was present in `issue-orchestrator`, which stated
  no boundary about where it works at all.
- `check_no_ledgers_in_repo()` guarded by file extension (sqlite/db/key) and so would
  not have caught issue text at all — extension-based guards do not cover this criterion.

## Status History

- 2026-08-11: drafted as proposed.
