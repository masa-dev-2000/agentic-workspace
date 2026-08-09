---
id: ledger-schema-before-use
statement: Any machine-consumed ledger or contract directory must have a documented schema and a validator before its first entry is written.
status: active
version: 1
---

## Rationale

Ledgers written by agents and read by agents drift immediately without a
contract: status enums fork, required fields go missing, and resolution
semantics live only in someone's head. A schema plus a mechanical check at
write time is cheap; retrofitting one after dozens of inconsistent entries is
not. The README (or schema doc) must also state the resolution direction —
who writes, who reads, and what state transitions mean.

## Scope

- criteria/, issues/, failure ledgers, handoff dirs — any directory whose
  files are parsed by scripts or agents.
- Requires: field list, allowed enum values, and a validator wired into the
  workspace check, all present before the first real entry.

## Counterexamples / Boundaries

- Free-form human notes and scratch dirs consumed only by humans are exempt.
- A minimal schema (frontmatter keys + status enum) is sufficient; this does
  not demand JSON Schema formalism where a checked prose contract works.
- An existing unschema'd ledger is not deleted — it gets a schema and a
  migration, and new entries must conform.

## Evidence

- issue #1: status enum mismatch between writer and validator
- issue #5: issues/ backend created without a schema
- issue #6: README missing resolution direction for ledger entries

## Status History

- 2026-08-09: drafted as proposed (bootstrap; axes reported by issue-ledger).
- 2026-08-09: activated. Approval: user (masa) explicit approval in Claude Code session, 「1,2. 承認」, 2026-08-09.
- 2026-08-09: Approval: actor=github:masa-dev-2000 channel=claude-code-session date=2026-08-09 ref="1,2. 承認"
