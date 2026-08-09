# Criteria Schema

Sibling document to `config/wiring.schema.md`, following the same style: this
is the contract that `check_criteria()` in `scripts/validate_workspace.py`
enforces mechanically. It has two parts: the pre-existing frontmatter/section
schema for `criteria/*.md`, and the structured approval-line format
introduced in Phase 6.

## File shape (unchanged, documented here for completeness)

Frontmatter (YAML between `---` fences):

| Field | Required | Type | Description |
|---|---|---|---|
| `id` | yes | string | Must match the filename stem. |
| `statement` | yes | string | One-sentence decision axis. |
| `status` | yes | string enum | One of `proposed`, `active`, `retired`, `graduated`. |
| `version` | no | number | Bumped on material rewrites. |

Body sections: `## Rationale`, `## Scope`, `## Counterexamples / Boundaries`,
`## Evidence`, `## Status History`. `check_criteria()` only enforces the
frontmatter fields and (for `status: active`) the structured approval line
described below; the section headings are a human convention, not
mechanically checked.

## Structured approval line (Phase 6)

Prior to Phase 6, `Status History` entries recorded approval as free prose,
e.g.:

```
- 2026-08-09: activated. Approval: user (masa) explicit approval in Claude Code session, 「1,2. 承認」, 2026-08-09.
```

This is unparseable and ambiguous about which identity "masa" refers to (a
GitHub account? a Claude Code session? a Slack handle?). Every criterion with
`status: active` must now ALSO carry at least one `Status History` line
matching this structured format:

```
Approval: actor=<provider>:<id> channel=<channel> date=<YYYY-MM-DD> ref="<short quote or reference>"
```

- `actor=<provider>:<id>` — namespaced identity, e.g. `github:masa-dev-2000`.
  The `<provider>:` prefix is the entire forward-compatibility story: it lets
  a future reader (or validator) distinguish a GitHub identity from a Slack
  identity, an email, or any other identity space, without inventing a new
  field. It does not imply those other providers are wired up yet — only
  that the id-space is namespaced so adding one later is not a breaking
  change.
- `channel=<channel>` — where the approval was given (e.g.
  `claude-code-session`, `github-pr-review`, `slack-dm`).
- `date=<YYYY-MM-DD>` — ISO date of the approval.
- `ref="<short quote or reference>"` — a short quote or pointer to the
  approval (e.g. the literal text the human typed, or a PR/comment URL).

The regex enforced by `check_criteria()`:

```
Approval:\s*actor=\S+\s+channel=\S+\s+date=\d{4}-\d{2}-\d{2}\s+ref="[^"]*"
```

The existing prose line is NOT removed when backfilling — both lines are kept
side by side in `Status History`, so the original human-readable record and
the machine-checkable one both exist. Do not fabricate a different date or
reference than what the prose line already records; the structured line must
describe the same approval event, just in parseable form.

## Deferred: roles and permissions

This schema intentionally does NOT define a roles/permissions matrix (who
may approve what kind of criterion, escalation paths, multi-approver
quorums, etc.). This repo currently has one human decision-maker. A
roles/permissions matrix is premature for a single-owner repo and is
explicitly **deferred until a second human identity exists** in this
workspace. A future reader should not mistake this absence for an oversight:
the `actor=<provider>:<id>` namespacing above is the only piece of
forward-compatible groundwork laid now; everything else (roles, quorums,
delegation) is out of scope until there is more than one approver to
distinguish.
