---
id: validator-signal-hygiene
statement: Validator warning baselines must be zero or explicitly acknowledged per finding, because a permanently noisy warn channel masks every new warning.
status: active
version: 1
---

## Rationale

A warn channel that always emits the same 22 lines trains everyone to ignore
it, so the marginal new warning — the one that matters — is invisible. Either a
finding is actionable (fix it or fail on it) or it is accepted (record the
acknowledgment in a baseline/suppression file with a reason). The steady-state
output of a healthy validator run is silence.

## Scope

- All validators and checks in this workspace that emit non-fatal warnings
  (validate_workspace.py and any future lint/CI hooks).
- Baseline/suppression mechanisms: each suppressed finding needs an identifier
  and a reason.

## Counterexamples / Boundaries

- Purely informational output (counts, timing) is not a warning and is exempt.
- A temporary nonzero baseline during a migration is acceptable if it is dated
  and shrinking; a static baseline that only grows violates this criterion.
- Does not mandate promoting every warning to an error — acknowledgment is a
  valid resolution.

## Evidence

- issue #7: 22 permanent warnings on every run mask new findings

## Status History

- 2026-08-09: drafted as proposed (bootstrap; axes reported by issue-ledger).
- 2026-08-09: activated. Approval: user (masa) explicit approval in Claude Code session, 「1,2. 承認」, 2026-08-09.
- 2026-08-09: Approval: actor=github:masa-dev-2000 channel=claude-code-session date=2026-08-09 ref="1,2. 承認"
