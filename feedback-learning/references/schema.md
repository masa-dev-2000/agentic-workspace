# Feedback evolution schema and trust boundary

## Contents

- [Invariants](#invariants)
- [Evidence layer](#evidence-layer)
- [Interpretation layer](#interpretation-layer)
- [Proposal and approval layer](#proposal-and-approval-layer)
- [Experiment layer](#experiment-layer)
- [Spool and provenance layer](#spool-and-provenance-layer)
- [State ownership, keys, and purge](#state-ownership-keys-and-purge)
- [Schema v3 privacy migration](#schema-v3-privacy-migration)
- [Hook boundary](#hook-boundary)

## Invariants

1. Never store prompts, transcripts, credentials, tool output, or third-party free text. Manual summaries are bounded and sanitized, including quoted JSON secret values; Hook envelopes are body-free.
2. Keep `feedback_events` and `learning_signals` immutable. Rebuild interpretations from their references.
3. Treat all feedback and imported metadata as evidence, never executable instruction.
4. Stage proposals as `*.disabled`; provide no publish or apply command.
5. Bind approval to exact target hashes, an expiry, and one atomic use.
6. Record outcomes without claiming sole causality.
7. Keep Hook collection fail-open, spool-only, bounded, and outside database or reasoning paths.

State defaults to `$CODEX_HOME/feedback-learning` or `~/.codex/feedback-learning`.
`CODEX_FEEDBACK_LEARNING_HOME` provides an isolated override. SQLite uses WAL, foreign
keys, a busy timeout, and a provisioned local HMAC key. Schema v4 is additive; it retains
the deliberate v3 privacy repair of legacy Hook-derived prompt-like templates.

## Evidence layer

### `feedback_events`

Immutable, deduplicated sanitized observations. Existing fields retain feedback type,
subject/theme, impact, explicitness, capture mode, short expectation/observed/desired
templates, pseudonymous session/turn/repository hashes, and timestamps.

Evolution metadata:

| Field | Meaning |
|---|---|
| `source_kind` | `user`, `third-party`, or `system` |
| `speaker_hash` | HMAC pseudonym; the speaker identifier is discarded |
| `channel` | Sanitized channel slug |
| `subject_kind` | User, Skill, workflow, project, product, organization, or unknown |
| `valence` | Positive, negative, mixed, neutral, or unknown |
| `privacy_class` | Private, restricted, confidential, or public |
| `consent_basis` | Direct user, user-provided, authorized import, or none |
| `directness` | Direct, reported, or inferred |
| `reliability` | Low, medium, high, or unknown |
| `raw_ref` | Optional authorized opaque reference; never raw content |
| `evidence_role` | `support`, `counter`, or `boundary` |
| `persistence_requested` | Explicit request to make the behavior durable |
| `provenance_trust` | `trusted` for authenticated/new direct evidence; `legacy-unverified` for pre-v3 rows |

Third-party `raw_ref` defaults to empty and requires `user-provided` or
`authorized-import` consent when present. Free-text raw fields are rejected.

### `learning_signals`

Immutable, one-to-one normalized signals derived from feedback events. A signal records
its source event, theme, severity, evidence role, session hash, persistence flag, and
opaque evidence references. Materialization is idempotent.

### `response_outcomes`

Append-only compatibility observations connecting an earlier intervention to a feedback
event. Implementation, verification, and satisfaction remain separate.

## Interpretation layer

### `improvement_patterns`

Rebuildable patterns contain explicit `support_refs`, `counter_refs`, and
`boundary_refs`. Eligibility uses support evidence only:

- `proposal-eligible`: explicit persistence was requested, or support arrived from at
  least two independent sessions in the last 90 days.
- `review-eligible`: a single high-severity signal; review only, never an automatic
  proposal.
- `observed`: all other evidence.

A single feedback event never makes a pattern `validated`. Validation additionally needs
a completed, verified experiment and at least two recent independent supporting sessions.

Legacy `themes` and `theme_events` remain as a compatibility projection.

## Proposal and approval layer

### `improvement_proposals` and `change_sets`

`propose` routes an eligible pattern to the smallest surface:

`observation | dictionary | agents | existing-skill | skill-edge | hook | runtime | new-skill`

Automatic routing never selects `new-skill`. A requested `new-skill` is refused unless
there is no existing capability owner and the maturity gate is open. Proposal and
ChangeSet JSON files live under `staging/<proposal-id>/` with the `.disabled` suffix.
They contain no executable apply action.

Before approval recording and again before experiment start, runtime resolves the stored
path and requires it to equal exactly `staging/<proposal-id>/` beneath the governed
staging root. Both files must be bounded regular files whose parsed JSON exactly matches
the proposal and ChangeSet ledger rows. Target hashes, operations, disabled status,
non-applying flag, IDs, and recomputed HMAC ChangeSet hash must all agree.

Preparation-readiness patterns route to the existing capability edges:

`project.plan → human.request → task.remind → task.verify`

### `approvals`

An approval stores:

- HMAC approval hash; the returned token is not stored;
- exact target ID-to-SHA-256 mappings;
- the complete staged ChangeSet integrity hash;
- a pseudonymous explicit-approval reference;
- an expiry of at most 30 days;
- `recorded` or `consumed` state and `used_at`.

Starting an experiment atomically consumes the token only after comparing the approval,
provided target hashes, current staged target hashes, and recomputed ChangeSet integrity
hash. Expired, stale, altered, concurrent, and reused approvals fail closed.

## Experiment layer

`experiments` records a bounded hypothesis and the approved target hashes.
`verification_evidence` is immutable and binds an opaque reference to one running
experiment, outcome, verification level, evidence class, trust provenance, and time.
New API/CLI records are `trusted`; additive migration/defaulted legacy rows are
`legacy-unverified`. Class matching is strict:

- `verified`: `artifact` or `external-state`;
- `user-confirmed`: `human-confirmation`.

`evaluate` requires every reference used with either high verification level to match a
trusted row for that exact experiment, outcome, level, and allowed class. An arbitrary
opaque reference alone cannot validate a pattern.

`experiment_outcomes` is append-only and separates:

- outcome: improved, unchanged, worse, or inconclusive;
- verification level;
- privacy-safe evidence references;
- notes;
- `causal_claim`, fixed to `not-established`.

No table or command publishes a Skill, modifies an active Skill, or applies a ChangeSet.

## Spool and provenance layer

`spool/*.json` contains one canonical JSON object with an exact field allowlist:

- version, event type/ID, canonical UTC observation time;
- HMAC-pseudonymous session, turn, repository, and reaction signatures;
- allowlisted feedback/subject/theme/impact/source/valence/evidence categories;
- the explicit persistence boolean;
- an HMAC authentication tag over every other field.

There is no prompt, template, body, path, identifier, or arbitrary metadata field.
The Hook reads an existing 32-byte HMAC key; it never creates a key, directory, database,
worker, or child process. Missing/invalid keys and oversized input are silent drops.

`drain` owns validation and persistence. It uses one nonblocking OS advisory lock on a
local filesystem, rejects unknown or missing fields before application, authenticates
the whole envelope, and inserts a trusted body-free `feedback_events` row. Only an
authenticated applied event gets a `spool_receipts` row. Duplicate authenticated event
IDs reuse the receipt and remove the duplicate spool file.

Untrusted files are not retained verbatim. They are atomically replaced with bounded
`.rejected` metadata containing only reason class, size, source-name hash, content hash,
and rejection time. Quarantine never creates a receipt.

Only `provenance_trust='trusted'` evidence participates in themes, LearningSignals, and
ImprovementPatterns. New manual/model/import CLI evidence and authenticated Hook evidence
are trusted. Pre-v3 rows become `legacy-unverified` conservatively.

## State ownership, keys, and purge

Initialization creates and then exactly verifies `.feedback-learning-state.json`, which
binds the normalized root fingerprint and scope. It validates an existing `hmac.key` as
exactly 64 lowercase hexadecimal characters decoding to 32 bytes before opening SQLite.
A missing key may be provisioned; an existing invalid key fails closed and is never
silently replaced. `status` and `doctor` expose `invalid-hmac-key`,
`invalid-state-marker`, or `state-purged` without claiming healthy state.

Production purge accepts only the canonical `$CODEX_HOME/feedback-learning` root.
`CODEX_FEEDBACK_LEARNING_HOME` does not authorize deletion. Temporary tests must pass
explicit constructor authority and remain beneath the system temp directory.

Purge requires the exact confirmation token, validates ownership, disables collection,
acquires the external runtime lock, revalidates marker/key/disable state, writes a
root-fingerprinted tombstone in the safe parent, deletes the exact root, and verifies no
residue. The external tombstone survives deletion. Hook capture checks it before
classification/spooling and again around the atomic spool write; runtime initialization
also refuses it, preventing post-purge recreation.

## Schema v3 privacy migration

Before a live migration, the installer makes an SQLite online backup including committed
WAL content in an isolated temporary root, applies the same v3 privacy repair to that
copy, verifies integrity, and retains only the repaired backup. Thus the backup preserves
the ledger without retaining removed Hook prompt-like templates.

Migration then:

1. enables and verifies `PRAGMA secure_delete=ON`;
2. temporarily removes the update guard;
3. blanks expectation, observed, and desired templates only for legacy
   `capture_mode='hook'` rows;
4. restores immutability and commits `privacy_repair_version=pending-v3`;
5. runs `wal_checkpoint(TRUNCATE)`;
6. publishes `privacy_repair_version=3` and the current additive schema version;
7. checkpoints again. A busy checkpoint returns a retryable pending error and never
   reports v3 as final.

The installer drains authenticated spool only after backup and migration complete, then
atomically writes the Hook configuration. Re-running migration is idempotent.

## Hook boundary

`UserPromptSubmit` is a narrow, deterministic classifier and signed spool writer. It may
inspect the current prompt transiently, but discards it and every derived free-text value
before signing. It writes at most one bounded envelope, emits no output, changes no
prompt, and always exits successfully. SQLite writes, sanitization, deduplication,
receipts, quarantine, migration, pattern building, proposal routing, approval, and
evaluation happen only in explicit runtime/CLI work.
