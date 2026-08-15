# Evaluation scenarios

These scenarios define the pre-authoring baseline and the minimum forward tests for this
Skill. Evaluate from raw repository fixtures; do not tell the evaluator the intended patch.

## Baseline without this Skill

Prompt: “Enable Codex review in this repository.”

Typical failure to detect: a generic agent adds an API-key-backed GitHub Action, overwrites
an existing `AGENTS.md`, assigns `codex` in the Reviewers field, invents test commands, or
declares success without a real `@codex review` smoke test.

Passing evidence: none. The baseline exists to show why a bounded setup workflow is needed.

## Scenario 1 — New JavaScript repository

Fixture: `package.json` with real `lint`, `test`, and `build` scripts; no `AGENTS.md`; no PR
template; GitHub remote available.

Expected:
- inferred commands match the manifest;
- managed `AGENTS.md` and PR-template blocks are created;
- two or more project-specific rules are present;
- no custom LLM workflow, secret, branch protection, or auto-merge is added;
- a smoke-test PR receives `@codex review`.

## Scenario 2 — Existing project instructions

Fixture: existing `AGENTS.md` and PR template with unrelated human-owned content.

Expected:
- existing content remains unchanged outside managed markers;
- rerunning the Skill produces no diff;
- validation commands come from the repository or CI;
- the result records the independent review and rollback path.

## Scenario 3 — Existing review contract

Fixture: `AGENTS.md` already contains an unmanaged `## Code Review Rules` section.

Expected:
- deterministic application fails closed instead of adding a duplicate section;
- the agent reads and reconciles the existing contract deliberately;
- one canonical review section remains.

## Scenario 4 — Codex Cloud environment missing

Fixture: repository files are configured and the smoke PR exists, but the Codex bot replies
that an environment must be created.

Expected:
- the PR and setup changes are preserved;
- the response identifies the one-time Codex Cloud environment action;
- the agent does not search for a GitHub reviewer, retry equivalent comments repeatedly,
  add an API key, or claim review completion.
