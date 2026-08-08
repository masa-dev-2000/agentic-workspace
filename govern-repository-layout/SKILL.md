---
name: govern-repository-layout
description: Audit and govern repository and workspace folder structure without moving or deleting files. Use when a repository has inconsistent output folders, unclear Git boundaries, misplaced generated artifacts, duplicate current and legacy directories, protected source-data siblings, or when Codex must propose, document, baseline, or validate a repository layout policy before reorganizing files.
---

# Govern Repository Layout

Separate discovery, policy, validation, and migration planning. Never reorganize first.

## Workflow

1. Read repository-local instructions and identify the requested workspace.
2. Run `scan` to discover real Git roots, top-level roles, deprecated paths, fixed references,
   protected external dependencies, and dirty state.
3. Run `init` to create a proposed `.repo-layout.yaml`. Treat it as a draft until the user approves
   its role assignments and exceptions.
4. Review the proposal against actual scripts, documentation, manifests, and path-sensitive jobs.
5. Create a baseline from the accepted audit when an existing repository already has violations.
6. Run `check`. Fail only for violations not present in the approved baseline.
7. Run `plan` to produce a migration plan. Require explicit approval before moving, deleting, or
   rewriting paths.

Use `references/manifest-schema.md` when editing a manifest.

## Commands

Resolve `scripts/repository_layout.py` relative to this file.

```powershell
python -X utf8 scripts/repository_layout.py scan `
  --root REPOSITORY_OR_WORKSPACE `
  --manifest OPTIONAL_MANIFEST `
  --output audit.json

python -X utf8 scripts/repository_layout.py init `
  --root REPOSITORY_OR_WORKSPACE `
  --output proposed.repo-layout.yaml

python -X utf8 scripts/repository_layout.py baseline `
  --audit audit.json `
  --output baseline.json

python -X utf8 scripts/repository_layout.py check `
  --root REPOSITORY_OR_WORKSPACE `
  --manifest proposed.repo-layout.yaml `
  --baseline OPTIONAL_BASELINE.json `
  --output verification.json

python -X utf8 scripts/repository_layout.py plan `
  --audit audit.json `
  --manifest proposed.repo-layout.yaml `
  --output migration-plan.md
```

All commands except explicit output-file creation are read-only. The CLI has no `apply`, `move`, or
`delete` operation.

## Policy principles

- Detect the real Git root; do not assume the current working directory is a repository.
- Distinguish an outer workspace from nested Git repositories.
- Classify by role before judging names: source, docs, planning, tests, fixtures, generated working
  output, deliverables, archive, cache/temp, and protected external inputs.
- Keep generated working output separate from approved deliverables.
- Allow repository-specific directory names through `.repo-layout.yaml`; do not impose `src/` or
  another universal tree.
- Preserve source evidence, client inputs, fixtures, manifests, and path-sensitive job bundles.
- Treat fixed path references and dirty worktrees as migration constraints.
- Baseline existing violations and reject only new violations during staged adoption.
- Never infer that an older file is disposable from its timestamp alone.

## Completion

Verify all of the following before reporting the Skill or a repository policy as complete:

- Skill structure validation passes.
- Fixture tests cover a normal repository, an outer workspace with a nested repository, a deprecated
  directory, a protected external path, and baseline-versus-new violation behavior.
- A representative real repository audit produces deterministic output.
- Scan and check do not change repository files.
- No protected path is proposed for automatic movement.
- The migration plan states approval and rollback requirements.
