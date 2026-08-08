---
name: implement-v2-work
description: Implement, fix, refactor, test, or review the kyoryokutai_support v2 application with required task selection, deterministic branch-lineage and migration-collision preflight, worktree isolation, specification priority, UX rules, security checks, verification gates, documentation updates, and release approval boundaries. Use for any change under v2/, including UI, API, authentication, database migrations, Cloudflare Workers/D1, tests, bugs, and production hotfix preparation.
---

# Implement v2 Work

When the lineage guard fails, or when maintaining this skill, read
`references/failure-cases.md` before changing the workflow.

Follow one implementation path from a ready task to verified handoff. Keep
`project-orchestrator` as the only project-management authority.

## 1. Establish the task contract

1. Read the applicable `AGENTS.md`, `TODO.md`, `v2/CLAUDE.md`, and affected specifications.
2. Read the AI Project Manager ledger. Treat it as the source of truth for live task status,
   assignee, dependency, and execution lease.
3. Select one ready task with a concrete outcome and verification rule. Record or resume its
   execution lease before editing.
4. Update `TODO.md` only when a durable outcome, priority, dependency, or exit criterion changes.
   Do not use it as a scratchpad for every implementation step.
5. Do not mark work complete from a plan, commit, or agent report alone. Require observable
   test, artifact, or external-state evidence.

## 2. Isolate the work

1. Inspect `git status`, branches, every relevant worktree, release evidence, and the production SHA.
2. Preserve all pre-existing dirty or untracked files.
3. Do not implement directly on `main`.
4. Use a dedicated branch and worktree for independent or parallel work. Never switch, reset,
   move, or clean a dirty worktree to make room.
5. Treat cleanliness as a safety property, not proof of authority. A clean local `develop`,
   `main`, or worktree HEAD is not an integration or production base merely because it is clean.
6. Resolve one exact base SHA from the user, ledger, verified production state, or current release
   evidence. Compare it with local and remote `develop`, `main`, release branches, and worktrees.
   If no authoritative source designates the SHA, stop before editing instead of guessing.
7. Refresh remote refs with `git fetch --prune origin`. Stop if it fails; stale remote-tracking
   refs are not valid lineage evidence.
8. Run the bundled lineage guard before creating the task branch:

   ```text
   node <skill-dir>/scripts/inspect-lineage.mjs \
     --project <project-root> \
     --base <exact-sha-or-ref> \
     --base-source user|ledger|production|release-evidence \
     --production <verified-deployed-sha>
   ```

   Use the installed directory containing this `SKILL.md` as `<skill-dir>`. Treat any nonzero
   result as a stop condition. The guard requires the verified production SHA to be an ancestor
   of the proposed base. Do not retry with another local branch until the failed authority or
   lineage assumption changes.
9. Base ordinary feature work on the verified integration SHA. The intended steady-state flow is:

   ```text
   feature branch -> reviewed PR -> develop -> staging
   develop -> human-approved release PR -> main -> production
   ```

10. Until staging and branch protection exist, do not claim the intended flow is enforced.
   For a production hotfix, branch from the verified deployed code SHA or its release branch,
   then forward-integrate the fix into `develop` and `main`.
11. Do not push, open a PR, merge, migrate a remote database, send external mail, or deploy unless
   the current task authorizes that specific action. Obtain explicit approval immediately before
   production or other consequential external changes.

## 3. Resolve the specification

Use this priority:

1. Current user instruction and applicable `AGENTS.md`
2. Verified production evidence and current architecture
3. Current milestone and exit criteria in `TODO.md`
4. Canonical role mock, `v2/docs/02_functional_spec.md`, and `v2/docs/06_adr.md`
5. `v2/CLAUDE.md` and `v2/docs/09_ops.md`

Keep `v1/` read-only. When documents conflict with verified code or production state, stop relying
on the stale statement, identify the conflict, and update the owning document with the implementation.
Do not silently revive Supabase-era or pre-mock behavior.

For UI changes, update the canonical mock first when behavior, wording, hierarchy, or navigation
changes. A bug fix that restores already-decided behavior does not require redesigning the mock.

## 4. Implement the smallest complete slice

- Preserve tenant, role, ownership, and state-transition boundaries.
- Add authentication and authorization tests with every protected API change.
- Enforce string, collection, upload, and AI input limits at both UI and API boundaries.
- Keep dates and month boundaries in JST.
- Add migrations; do not rewrite already-applied migrations.
- Before choosing a migration filename, retrieve the live remote D1 migration list as JSON into an
  access-limited temporary file. Rerun the lineage guard with
  `--migration <NNNN_description.sql> --remote-migrations-file <absolute-json-path>`. The guard
  scans the remote list plus migration names across all registered worktrees. Never infer the next
  number from the active worktree alone. Stop when remote evidence is missing, a remote migration
  is absent from Git, a number maps to different filenames anywhere, the proposal reuses a number,
  or it is not the next verified number. Delete the exact temporary file after verification.
- For production-bound schema work, additionally verify the remote D1 migration list and release
  evidence. The guard does not turn local files into proof of remote state.
- Implement SQLite and D1 behavior consistently where both paths exist.
- Keep development and CI deterministic: mock AI, console mail, and no production data.
- Never log secrets, tokens, invite URLs, mail bodies, addresses, or raw user/AI content.

For UI work:

- Minimize user input, decisions, navigation, and learning cost.
- Keep primary information and actions within the intended viewport.
- Avoid body scrolling. Use constrained progressive disclosure only when necessary.
- Preserve readable text and at least 44px touch targets.
- Do not add explanatory copy to compensate for unclear structure.

## 5. Verify by change risk

Always run:

1. Targeted tests for changed behavior and its failure path
2. `npm run lint`
3. `npm run typecheck`
4. `git diff --check`

Also run:

- Full `npm test` for shared code, API, schema, authentication, authorization, or release changes
- `npm run build` for runtime or route changes
- `npm run build:worker` and `npm run check:worker-bundle` for Worker/D1/release changes
- Local Worker smoke for Worker-facing changes
- Real-browser verification for every changed user flow
- The lineage guard again before handoff when the base, worktrees, release state, or migration set
  changed during implementation

For changed UI, capture real images at the relevant set of 375x812, 768x1024, 1024x768, and
1440x900. Verify no body scroll, horizontal overflow, clipping, fixed-element overlap, broken
spacing, or browser warnings/errors. Do not declare UI complete from DOM assertions alone.

## 6. Update the owning records

- `TODO.md`: durable outcome, dependency, exit criterion, and verified completion
- AI Project Manager ledger: live status, assignee, lease, and evidence reference
- `v2/docs/06_adr.md`: new decisions only
- Canonical mock and functional specification: changed user-visible contract
- `v2/docs/07_work_log.md`: completed implementation and verification summary
- Release evidence/roadmap: deployment and external-state verification only

Avoid duplicating the same fact. Link to its owner.

## 7. Hand off

1. Review the diff for unrelated edits, secrets, generated debris, and user-owned changes.
2. Commit only the intended files on the task branch.
3. Report branch, commit SHA, changed files, verification results, external changes, blockers,
   and the next dependency.
4. Before production, identify the exact deploy SHA, bindings, migrations, rollback target, and
   command; obtain explicit approval at the execution boundary.
5. Close the execution lease only after verification. Leave incomplete work as active,
   verification, waiting-human, or blocked with a concrete reason.

When this Skill changes, do not promote the update from syntax validation alone. Run fresh-agent
forward tests for at least: a viewport UI fix, a divergent integration base with a
production-only migration, and a production hotfix. Keep the global version unchanged until all
three preserve the intended branch, migration, verification, and approval boundaries.
