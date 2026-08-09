---
id: drift-coverage-completeness
statement: Every synced directory and every path referenced from outside the workspace must be covered by the drift validator, with no hardcoded allowlists that silently exclude new entries.
status: proposed
version: 1
---

## Rationale

Drift checking only prevents divergence for paths it actually watches. Hardcoded
script lists, pinned versioned paths (e.g. a specific uv Python), and external
plugin paths that the validator never inspects create blind spots where drift
accumulates unnoticed until something breaks in the live environment. Coverage
should be derived (glob/enumerate) rather than enumerated by hand, so adding a
new hook or plugin automatically brings it under validation.

## Scope

- `scripts/validate_workspace.py` drift checks and any future drift tooling.
- Copy-synced directories (hooks/, commands/, config/) and their live targets.
- Any path outside the repo that repo content references (plugins, interpreter
  paths, external tool locations).

## Counterexamples / Boundaries

- Paths that are intentionally environment-local and documented as unmanaged
  (e.g. machine-specific caches) need not be drift-checked, but the exclusion
  must be explicit and written down.
- Does not require validating content of third-party binaries — existence and
  reference integrity is enough for external paths.

## Evidence

- issue #2: hardcoded script list misses newly added hooks
- issue #3: versioned uv Python path pinned, drifts on upgrade
- issue #4: external plugin path referenced but never checked

## Status History

- 2026-08-09: drafted as proposed (bootstrap; axes reported by issue-ledger).
