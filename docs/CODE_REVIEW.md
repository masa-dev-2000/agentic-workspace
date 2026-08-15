# Code review pipeline

This workspace separates implementation, verification, independent technical review, external PR
review, and product approval. No single model or vendor is treated as both author and final judge.

## The layers

| Layer | Owner | Purpose | Required evidence |
|---|---|---|---|
| 0. Deterministic gate | CI and repository scripts | Catch schema, wiring, test, and mechanical regressions | Actual command output from the validator and tests |
| 1. Implementation verification | The agent that changed the code | Prove the requested behavior works | Reproduction, tests, build, or direct execution performed in the implementation session |
| 2. Independent agent review | A fresh read-only reviewer | Look for introduced defects without inheriting the implementer's assumptions | Findings from `review-agent` or `adversarial-reviewer`, including the target and round count |
| 3. External PR review | CodeRabbit when installed | Apply a persistent, repository-wide review process to every non-draft PR | PR review comments and zero unresolved critical/major issues, or an explicit `not configured` record |
| 4. Owner acceptance | Human owner | Decide whether the behavior, UX, scope, and remaining risk are acceptable | Approve, request changes, or an explicit comment when GitHub cannot record a self-approval |

The canonical flow is:

```text
implement
  -> execute verification
  -> independent read-only review
  -> fix introduced medium-or-higher findings
  -> re-review (maximum three review rounds)
  -> open PR with evidence
  -> CI + optional CodeRabbit review
  -> human owner decision
  -> merge through the project's normal flow
```

## Layer 0: deterministic gate

This repository currently runs:

```text
python -X utf8 scripts/validate_workspace.py --no-live
python -X utf8 -m unittest discover -s scripts/tests -v
```

The local pre-push hook runs the fuller machine-aware variant described in `README.md`. A green AI
review never overrides a failed deterministic check.

## Layer 1: implementation verification

The implementation agent owns the change and therefore owns the first proof that it works.

- For a bug, reproduce the failure before fixing it whenever possible.
- Run the acceptance checks in the same session that made the change.
- Report the real command and result. "Should work" is not evidence.
- Do not use a review model as a substitute for tests or execution.

## Layer 2: independent agent review

Use a fresh context that did not implement the change.

- Claude Code: launch `adversarial-reviewer`.
- Skills-compatible runtimes: invoke `$review-agent` explicitly.
- The reviewer is read-only and defect-first.
- The implementer validates each finding against the source before changing code.
- Address introduced findings of medium severity or higher, then re-run the independent review.
- Stop after three review rounds and record any remaining disagreement or residual risk honestly.

A reviewer must not modify files, commit, push, post PR comments, or approve its own change. This
keeps diagnosis separate from remediation and prevents a single context from silently marking its
own work clean.

## Layer 3: CodeRabbit PR review

`.coderabbit.yaml` is version-controlled policy only. It does nothing until the CodeRabbit GitHub
App is installed for the repository. CodeRabbit is an optional external reviewer, not a dependency
of the local workflow.

Once installed:

- every non-draft PR targeting the default branch is reviewed automatically;
- new pushes receive incremental reviews until CodeRabbit's configured pause threshold;
- `@coderabbitai review` requests another incremental review;
- `@coderabbitai full review` requests a fresh full review.

Do not apply an autofix merely because CodeRabbit proposed it. The implementation agent must first
confirm that the finding is real, in scope, and compatible with the acceptance criteria. CodeRabbit
may be wrong, and it does not own product decisions.

If CodeRabbit is not installed or unavailable, record `not configured` or the exact failure in the
PR template. Layers 0, 1, 2, and 4 still form a complete review path.

## Layer 4: owner acceptance

The human owner decides matters that automated reviewers cannot own:

- whether the implementation solves the intended problem;
- whether the UX and operational trade-offs are acceptable;
- whether a residual risk is worth accepting;
- whether the scope should change;
- whether and when to merge or deploy.

`/kio-review` is the owner-facing explanation and decision step. It must not represent a passing CI
run or an AI review as human approval.

## Merge readiness

A PR is ready for the owner decision when all of the following are true:

1. Acceptance criteria are linked or restated in the PR.
2. Verification was executed and its result is recorded.
3. Independent review completed with no unresolved introduced medium-or-higher findings, or the
   remaining findings and rationale are recorded after the three-round cap.
4. Required CI checks are green.
5. When CodeRabbit is installed, no unresolved critical/major review issue remains, or the owner is
   explicitly shown the exception and rationale.
6. Rollback or recovery is described when the change has meaningful operational risk.

Low-severity or pre-existing findings do not block automatically. They become a follow-up issue when
they are real, actionable, and outside the current scope.

## Why both an agent reviewer and CodeRabbit?

The independent local agent can understand the issue, run repository commands, inspect broad
context, and review before the PR exists. CodeRabbit supplies a persistent GitHub-level checkpoint,
incremental review on later pushes, and review history independent of the implementation session.
They overlap intentionally, but they fail in different ways.

The intended division is:

```text
independent agent = deep pre-PR reasoning
CodeRabbit        = always-on post-PR inspection
CI                = deterministic proof
human owner       = product and risk decision
```
