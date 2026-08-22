---
name: setup-codex-review
description: Set up or repair native Codex pull-request review in a Git repository by inspecting its real validation path, adding idempotent repository-specific AGENTS.md and pull-request-template blocks, validating the result, and optionally opening a smoke-test PR that triggers @codex review. Use when enabling Codex review in a new or existing GitHub repo. Do not use to review a specific diff; use review-agent instead.
---

# Setup Codex Review

Turn the current or named Git repository into a working Codex review target. Preserve
existing project instructions and CI. Do not add CodeRabbit, an OpenAI API key, a custom
LLM review Action, branch protection, or auto-merge.

## Workflow

1. **Resolve the target.**
   - Use the repository explicitly named by the user; otherwise use the current Git root.
   - Read every applicable `AGENTS.md`, the README, manifests, existing CI workflows, and
     any pull-request template before editing.
   - Run the bundled `scripts/setup_codex_review.py scan --repo <target>` and retain its
     JSON as evidence.

2. **Derive the repository contract.**
   - Select only validation commands that actually exist in the repository or CI. Never
     invent a command merely because the language commonly uses it.
   - Write two to five high-signal review rules from real project boundaries such as public
     APIs, authentication and authorization, migrations, destructive state changes,
     generated sources, concurrency, billing, or data locality.
   - Do not restate lint, formatting, or schema checks already enforced deterministically.
   - Each rule must name a concrete failure class and the safe path. Generic advice such as
     “follow best practices” is not a review rule.

3. **Scaffold idempotently.**
   - Create a temporary JSON config with `validation_commands` and `review_rules`.
   - Run the bundled script in dry-run mode first. Inspect the complete diff.
   - Run it again with `apply ... --write`, then run `check` with the same config.
   - The script owns only marked blocks in root `AGENTS.md` and the existing or canonical
     `.github/pull_request_template.md`; content outside those markers must survive byte
     for byte.
   - If an unmanaged `## Code Review Rules` section already exists, stop deterministic
     application and merge it deliberately. Never create a second competing section.

4. **Verify the target.**
   - Execute every selected validation command and any change-specific check.
   - Run a separate read-only review with `$review-agent` or Codex `/review` against the
     actual merge diff.
   - Do not report setup complete when a command was skipped or failed. State the precise
     unverified surface.

5. **Publish and smoke-test when GitHub review was requested.**
   - Require a GitHub remote, authenticated `gh`, and a branch containing only the setup
     changes. Do not include unrelated working-tree changes.
   - Push the branch, open or update one PR, and comment exactly `@codex review`.
   - Do not search for or assign Codex in GitHub’s Reviewers field; Codex is a GitHub App.
   - Treat the eye reaction as processing. Completion is a Codex review, a thumbs-up
     reaction, or an explicit blocker.
   - If the bot says to create an environment for the repository, preserve the PR and
     return that one-time Codex Cloud action as the only manual blocker. Repository files
     cannot create a Codex Cloud environment.
   - Never merge the smoke-test PR.

## Completion evidence

Return:

- target repository and default branch;
- files changed, with confirmation that unmanaged content was preserved;
- validation commands and their observed results;
- the independent review result;
- PR URL and Codex review status when published;
- any one-time Codex Cloud environment blocker;
- residual risks and rollback path.

“Files exist” is not completion. The setup is complete only when the generated blocks are
current, selected validation passes, and either Codex reviews the smoke PR or the exact
external environment blocker is proven.
