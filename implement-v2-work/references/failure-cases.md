# Verified failure cases

## F-001: stale integration base reused a deployed migration number

Observed on 2026-07-30:

- The selected integration branch was clean but did not contain the deployed
  production lineage.
- A release and production already contained migration `0009`, while the
  selected branch ended at `0008`.
- Planning and implementation treated local branch cleanliness and internal
  consistency as sufficient evidence.

Root cause:

- The workflow required inspection but did not define a fail-closed ancestry
  check.
- Migration numbering was checked only inside the selected worktree instead
  of across registered worktrees, relevant Git refs, and the remote D1 ledger.
- Validation covered syntax and formatting but not a forward regression where
  production had advanced independently.

Required prevention:

- Resolve the exact verified production SHA and require it to be an ancestor
  of the implementation base before editing.
- Build the migration-number union from registered worktrees, relevant refs,
  and live remote D1 evidence.
- Reject reused prefixes, different filenames sharing a prefix, missing remote
  evidence, and any proposal other than the verified maximum plus one.
- Re-run the guard before handoff if the base, production SHA, worktrees, refs,
  or remote migration ledger changed.

Regression cases:

- A stale `develop` branch against a newer production SHA must fail.
- A proposed migration using an existing prefix must fail.
- A remote migration absent from registered Git/worktrees must fail.
- The verified next prefix on a base containing production must pass.
