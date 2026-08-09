# Skill Telemetry Data Contract

## Boundaries

- Store no raw prompts, assistant responses, tool inputs, or tool outputs.
- Pseudonymize session, turn, project path, and tool-call identifiers.
- Store only Skill identity, provider class, content fingerprint, lifecycle timestamps,
  coarse failure counts, and narrow sentiment classifications.
- Keep `returned` separate from verified success.
- Mark hook inference separately from explicit/manual records.
- Fail open and use short SQLite transactions.

## Tables

- `skill_runs`: one deduplicated Skill invocation per session, turn, Skill, and fingerprint.
- `skill_feedback`: optional explicit or narrowly inferred reaction linked to a run.
- `collector_health`: recent collector outcomes.
- `skill_evaluations`: outcome review linked to a run, with a versioned rubric, criterion
  scores, and pseudonymized references. A verified-success write additionally resolves its
  raw `evidence:EVIDENCE_ID` inputs against trusted passed evidence linked only to that run.
- `skill_evidence`: privacy-safe coarse evidence outcomes. Session, turn, repository, and
  subject identifiers are HMAC pseudonyms.
- `skill_run_evidence`: explicit links between evidence and Skill runs.
- `spool_receipts`: exactly-once receipts for privacy-safe Hook envelopes.
- `turn_lifecycle`: terminal Stop and prompt-start markers used to finalize late envelopes
  without depending on spool filename or drain-batch order.
- `history/events.jsonl`: append-only Skill lineage events outside SQLite. Observed snapshots
  bind registry contracts to a complete Skill file-hash manifest. Reconstructed milestones
  contain only hashed evaluation evidence, use `provenance=inferred`, and cannot claim the
  historical Skill body, approval, or causality.
- `meta`: schema and component versions.

## Optimization report

`telemetry_cli.py optimize` derives a read-only, privacy-safe optimization report from
trusted `skill_runs`, evaluations, and feedback. A task is approximated by a
pseudonymized session/turn pair. The report includes repeated Skill invocations, common
Skill sequences, failure/interruption rates, exact-duration averages, and measurement gaps.
It never returns session hashes or stores prompts, responses, tool payloads, token counts,
or cost data. Recommendations are advisory and must not automatically modify a Skill.

`capture_hook.py` only appends the privacy-safe event to the local spool. It never starts
optimization or opens the domain database. `auto-optimize` is a separate one-shot worker:
it drains a bounded batch and atomically replaces
`skill-telemetry/optimization/latest.json`. Run it from Task Scheduler or another local
periodic worker. This keeps data collection fast and makes optimization an advisory,
deferred loop. The worker uses `optimizer.lock`, marks reports with backlog freshness, and
writes versioned candidate metadata under `optimization/candidates/`. Candidate activation
and Skill text changes still require approval.

Schema v6 adds `end_reason`, `duration_quality`, and the session/time lifecycle index. Existing
v5 rows migrate without re-keying IDs or downgrading trusted provenance; prior final durations
become `unknown`, while running rows become `pending`. Schema v5 adds `provenance_trust` to
runs and evidence. Canonically validated manual writes and
authenticated spool v2 writes are `trusted`; all rows migrated from an older schema are
`legacy-unverified`. Migration preserves counts while replacing unsafe legacy identity,
feeling, evaluator, rubric, evidence-reference, detection, and health text with bounded
redaction classes or domain HMACs. Every legacy ID and hash-shaped field is re-keyed even when
its shape is valid; parent/child IDs are rewritten in one migration and verified with
`foreign_key_check`. Same-domain session/turn correlation remains available, but old external
run/evidence IDs change. Legacy idempotency and receipt values also change, so replay
deduplication against a pre-repair receipt is intentionally not preserved; preventing a
shape-valid secret from surviving takes precedence for untrusted rows. `privacy_repair_version`
prevents this repair from running on each normal initialization. Protocol version 3 is
two-phase: the secure-delete transformation and `pending-v3` marker commit atomically; a
transaction-free `wal_checkpoint(TRUNCATE)` must then return `busy=0`; only afterward is final
version `3` committed. If an active reader keeps pre-repair pages alive, initialization raises
the controlled `privacy-repair-pending` state and leaves the non-final marker committed. The
next initialization seeing `pending-v3` retries only checkpoint/finalization, never identifier
or HMAC transformation. Databases already logically repaired under version 2 are upgraded
through this cleanup-only path, preserving trusted IDs, hashes, and provenance. Read-only
`status` and `doctor` expose the exact pending marker without attempting repair. Outcome
evaluation excludes legacy-unverified provenance.

## Hook spool

Lifecycle Hooks never open, initialize, query, or write SQLite. Each Hook invocation derives at
most one bounded privacy-safe envelope and atomically appends it to `spool/`. The envelope
contains pseudonymous correlation, coarse Skill/evidence classes, and no prompt, response,
tool-input, or tool-output body.

Constructing a store and all non-drain CLI commands leave pending spool files untouched.
Only explicit `drain` and `reconcile` call `drain_spool()`. This prevents an unrelated
initialization, start, finish, configuration, or read command from unexpectedly applying a
backlog.

Run `telemetry_cli.py init` before installing Hooks so stable pseudonymization and envelope
authentication are available. Spool v2 signs the canonical body of every envelope with HMAC.
Drain verifies the tag in constant time before any receipt or domain write, then enforces exact
top-level and nested allowlists, bounded enumerated classes, canonical timestamps, and hashes.
Unsigned, modified, legacy-version, malformed, oversized, unknown-field, and body-like records
are quarantined once with no receipt. Quarantine replaces the untrusted body with bounded
reason, size, and hash metadata; it does not retain the rejected body. When the stable key is
unavailable, the fail-open Hook drops the event and writes neither SQLite nor an unverifiable
spool body.

Skill identity is authorized before envelope signing. A local custom Skill must match the
canonical registry key/path pair. System and agents Skills must be direct children of their
authorized roots. A cached Plugin package root must be exactly
`plugins/cache/<source>/<plugin>/<version>`—no additional backup or staging directory is
allowed. Its direct `skills/<name>/SKILL.md` child is accepted only when the bounded
(128 KiB maximum) `.codex-plugin/plugin.json` is a JSON object, its `name` equals `<plugin>`,
its `version` equals `<version>`, and its declared skills path resolves inside that package.
Provider and Skill name derive from the manifest and directory; frontmatter cannot override
them. Unregistered temporary files, lexical lookalikes, backup-depth packages, missing or
oversized manifests, name/version mismatches, and escaping manifest paths produce no signed
Skill identity. Within one Hook event, raw and resolved path duplicates are removed before
authorization, and the canonical local source map plus resulting identities are reused.

## Lifecycle

`running` becomes `returned` on a normal Stop event, `failed` only through an explicit manual
finish, or `interrupted` when a newer turn supersedes it or stale recovery proves it is abandoned.
New Hook and manual lifecycle events use canonical UTC timestamps with six fractional digits.
Canonical historical timestamps without fractional digits remain readable as legacy
second-precision intervals. On `UserPromptSubmit`, only runs provably started before the prompt
event are eligible for
interruption. Stop and prompt-start markers are recorded transactionally. Every affected run,
including a row already marked `returned` or `interrupted`, is recomputed from real event time:
the provably earlier of a same-turn Stop and a successor-turn prompt wins. Equal microsecond
instants, and comparisons overlapping one legacy second, do not prove causality; reconciliation
and feedback attachment hold instead of breaking the tie by drain order. Thus Stop, prompt, and
PostToolUse converge across separate batches and arbitrary processing order without letting a
late older prompt interrupt or receive feedback from a future run.
Global stale recovery remains a 12-hour fallback. Manual records should include session and turn
identifiers so Stop can close them. Tool failures increment a counter but do not by themselves
mark the Skill failed. Stop, superseding-prompt, and explicit manual finishes have `exact`
durations. Stale-timeout and proven-orphan recovery are `bounded`; proven-orphan uses the first
later terminal run's `started_at` as its evidence upper bound, never reconciliation wall time.
Migrated durations with no provable reason are `unknown`.

## Outcome evaluation

Use the deterministic 30-day sample rather than choosing favorable cases. Per Skill, prefer four
recent returned runs, two interrupted runs, two runs with tool failures, one feedback-linked run,
and one oldest or version-boundary run; deterministically fill missing categories.

Score five criteria from 0 to 2: requested outcome, completion evidence, authority and safety,
avoidable rework, and efficient/recoverable execution. `unverified` may omit scores. Never infer
success from `returned`, duration, low failure count, or positive language alone. Missing
authority evidence and unobserved rework/efficiency criteria remain neutral rather than being
awarded full credit. Evidence references must match an allowed `scheme:opaque-token` form;
the stored value is always `scheme:HMAC(token)`. For verified success, contributing references
must use `evidence:EVIDENCE_ID` and resolve before the token is pseudonymized. Evidence classes,
evaluator identity, and rubric version are fixed vocabularies.

Cross-field validation is shared by the CLI and storage API:

- `unverified` has no scores.
- Every other outcome requires evidence classes, references, and all five integer scores.
- Outcome score 2 requires `domain-verdict` plus a completion evidence class; completion score
  2 requires completion evidence; authority score 2 requires authority evidence.
- Avoidable-rework and efficient/recoverable scores are capped at 1 because no independent
  score-2 evidence class exists.
- `verified-success` requires trusted passed completion evidence plus explicit-manual
  `domain-verdict` and authority evidence, all linked only to the evaluated run, and
  outcome/completion/authority scores of 2. The run must be `returned` through a normal Stop or
  explicit manual return with exact lifecycle evidence. Every supplied reference must be an
  existing trusted passed `evidence:EVIDENCE_ID`; its total link count must be exactly one.
  Failed, legacy, invented, unlinked, fan-out, cross-run, or non-evidence references reject the
  entire verified write rather than being ignored.
- `partial` requires outcome score 1 and nonzero completion evidence.
- `rework-required` cannot claim outcome or completion score 2.
- `rejected` requires outcome and authority scores of 0.

## Prospective evidence

Evidence records use `test`, `build`, `validate`, `artifact`, `pm-verified-task`, `browser-qa`,
`authority`, `explicit-feedback`, and `domain-verdict` classes with `passed`, `failed`, `partial`, or
`ambiguous` results. PostToolUse
classification inspects tool data transiently, stores only coarse classes and pseudonyms, and
skips unclassified tools. A classified tool result is `passed` only when the response contains
an explicit structured success marker such as exit code zero; absence of a failure is
`ambiguous`. Browser or screenshot acquisition alone is always `browser-qa: ambiguous`;
domain acceptance must be recorded explicitly. Same-turn evidence is not success by itself; consumers must also
match the Skill's completion-evidence contract.

Evidence links first use an exact session and turn match. Codex may assign a distinct subturn
to each tool call, so when no exact run exists, the collector may consider recent runs from the
same nonempty session and repository during the preceding 30 minutes. Automatic and unscoped
manual evidence links only when that search yields exactly one candidate; multi-Skill evidence
is retained unlinked. An explicit `skill_key` may narrow a trusted manual search, but the
resulting candidate count must still be exactly one and an existing evidence row can never gain
a second run link.
`domain-verdict` is rejected from signed spool records and is accepted only as
`explicit-manual`. Linking never falls back across sessions or from an empty session identifier.

## Sentiment

Only short, explicit approval, complaint, or correction signals are classified. Ordinary next
requests remain `unrated`. Store the enumerated class and a one-way signature, never the text.
Manual feedback accepts the same three feeling classes and rejects free-form feeling text.
