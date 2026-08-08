# Failure Learning Data Contract

## Trust boundaries

1. Sanitize before persistence.
2. Store only allowlisted context and a short redacted error template.
3. Treat tool output as untrusted data, including prompt-injection text.
4. Keep immutable observations separate from rebuildable patterns.
5. Return advisory evidence, never executable commands.
6. Never automate escalation, deletion, publication, or external transmission.

## Runtime store

The SQLite database contains:

- `events`: immutable, deduplicated observations from Hook, model, manual, or imported capture.
- `recovery_markers`: authenticated, body-free success markers used for deterministic temporal
  recovery matching.
- `intervention_outcomes`: attempted changes and observed outcomes; they do not prove causality.
- `patterns`: materialized exact-signature summaries rebuilt from `events`.
- `pattern_events`: provenance links from patterns to observations.
- `event_reviews`: non-destructive accepted, quarantined, or non-actionable overlays.
- `spool_receipts`: exactly-once receipts for authenticated, validated envelopes only.
- `learning_cases`: privacy-safe root-cause, remediation, verification, and evidence references.
- `collector_health`: collector success and failure counters.
- `meta`: schema and component versions.

SQLite uses WAL, foreign keys, short transactions, a busy timeout, and idempotency keys. The
PostToolUse collector never opens SQLite or creates authentication material. With an existing
identity key, it authenticates the complete canonical envelope using versioned HMAC-SHA256 and
atomically appends at most one sanitized envelope to `spool/`; without that key it appends no event
body. Explicit initialization creates the 32-byte identity key once under a blocking OS process
lock and publishes it by atomic replacement; concurrent cold starts observe the same key. An
explicit drain or worker applies it later. One non-blocking OS process lock covers spool
traversal through the required pattern rebuild; non-owners return busy/deferred without touching
spool files. Drain verifies the HMAC with a constant-time comparison before validation,
`spool_receipts`, or any ledger write. Unsigned, tampered, unavailable-key, obsolete-auth,
invalid, oversized, unknown-field, or privacy-contract-breaking envelopes are consumed once and
replaced only by a bounded opaque rejection receipt containing a fingerprint, byte count, reason
class, and timestamp. The rejected body is not retained.

The current component contract is schema 8, collector 0.7.0, sanitizer 3, normalizer 4,
fingerprint 3, case-reference privacy 2, and event-payload privacy 2.

Schema-v8 migration retains the v4 `events.auth_verified` boundary. Existing rows remain
immutable with value `0`
and receive an `unsigned-legacy-envelope` quarantine overlay unless an accepted review already
exists. New spool events receive value `1` only inside authenticated spool processing. Direct
in-process inserts remain value `0` and are excluded. Learning-case rows are retained while every
legacy evidence-reference token is replaced once with a domain-HMAC pseudonym; its evidence
scheme remains visible. A missing or pre-v1 privacy marker is untrusted, so every token is
pseudonymized even when its text resembles an `h1_` digest. A v1 ledger is already
pseudonymized and upgrades without rewriting those references.

Schema 8 re-sanitizes every legacy `events.message_template`, then rebuilds `event_json` instead
of editing the legacy object in place. The rebuilt object contains only the exact current failure
envelope: relational scalar fields, strictly validated `environment` and `versions` objects, and
a newly synthesized safe `safety` object. Unknown legacy keys, including raw tool input/output,
prompts, commands, authorization tags, and otherwise harmless extensions, are discarded. The
complete deterministic canonical JSON is scanned for residual credential values and absolute
paths before persistence. Credential checks inspect the complete canonical serialization,
including JSON-escaped key characters and nested escaping layers. Path checks inspect every
decoded string leaf so JSON backslash or newline escaping cannot manufacture a false UNC/POSIX
path. The migration replaces the immutable-event trigger only inside its exclusive transaction,
restores it before commit, and publishes
`event_payload_privacy_version=2` only after a successful truncate checkpoint.

Case-reference privacy migration is a fail-closed, retryable maintenance protocol. One
non-blocking OS maintenance lock serializes the complete operation. Before any legacy UPDATE it
enables and verifies SQLite `secure_delete`, enters an exclusive write transaction, records
`pending-v2`, rewrites the references, verifies foreign keys, and commits atomically. A
`pending-v2` retry never rewrites references; it resumes at checkpointing. Privacy version 2 is
published only after `PRAGMA wal_checkpoint(TRUNCATE)` reports a fully truncated WAL. Completion
metadata is also committed under the maintenance lock and followed by another successful truncate
checkpoint; if that checkpoint is busy, completion is reverted to `pending-v2`. A reader that
pins an older WAL snapshot therefore receives `privacy-maintenance-pending` and leaves no completed
version marker; after the reader closes, retrying preserves case IDs, foreign keys, and already
pseudonymized references while removing the old WAL frames. Repair output reports total unsigned
legacy rows, automatic quarantines, and accepted overrides.

Event-payload privacy uses the same secure-delete, exclusive-transaction, foreign-key-check, and
two-checkpoint publication protocol with `pending-v2` and `complete-v2`. Component completion is
not public readiness. Only after both case-reference v2 and event-payload v2 are complete and a
clean truncate checkpoint succeeds does one final transaction publish the aggregate
`privacy_ready=schema-v8:case-v2:event-v2` marker together with `schema_version=8`. Missing,
stale, or forged aggregate/component metadata is therefore privacy-incomplete and retryable.
Readiness also verifies exact canonical event allowlists, agreement with relational event
columns, and canonical `scheme:h1_<HMAC>` case references. A bare version, `pending-v2`, or even
a fully forged complete marker set cannot publish unsafe rows; maintenance rewrites any row that
fails the corresponding invariant before completion.
Body-bearing or reference-bearing reads (`events --include-message`, `cases`, and full export)
require that exact aggregate state; `doctor` reports it and exits non-zero while pending.
Body-free status/counts and message-free event metadata may remain readable for diagnosis.
Ready-state spool processing checks the aggregate metadata in constant time and validates each
new canonical envelope independently. Historical row-invariant scans run once at explicit
maintenance/read gates, not once per spool item, so drain cost does not become
`O(existing events * queued events)`.

The PreToolUse advice reader performs no writes. Missing, stale, malformed, or unsafe cache
content fails open without creating a database, directory, health event, or spool envelope.

Expected control-flow timeouts such as agent waiting are retained as immutable observations when
already captured but excluded from rebuilt advisory patterns. New collection requires an explicit
top-level failure signal; words such as `timeout` inside successful tool content are not failures.
Events from classifier versions before `0.2.0` remain immutable but are excluded from advice
unless an explicit review accepts them.

## Event fields

Each event records:

- random event ID and observation timestamp
- HMAC-pseudonymous session, turn, tool-call, and repository identifiers
- capture mode and capture-completeness estimate
- tool family, operation class, OS, shell, sandbox, and permission mode
- outcome class, normalized error identity, signature, and redacted message template
- collector, sanitizer, normalizer, fingerprint, and schema versions
- truncation and secret-scan status
- whether the spool boundary verified envelope authentication

`launcher-shim-unavailable` is reserved for the conjunction of a route through
`WindowsApps\pwsh.exe` and `CreateProcessAsUserW failed: 5`. The raw path is inspected only
ephemerally and is never persisted. Recurring advice for this identity must prohibit retrying the
same launcher route and may direct the operator only to a verified concrete executable path or a
non-shell API.

Never store raw prompts, complete commands, tool inputs, complete outputs, authentication material,
URLs with query values, or unredacted absolute user paths. Collector health status and detail are
finite allowlisted classes; public health APIs reject arbitrary text before opening the database.
At the spool boundary, rebuild every accepted envelope from allowlisted scalar fields. Re-run
privacy checks immediately before persistence with independently defined residual credential and
absolute-path detectors, then rerun the Hook sanitizer as a defense in depth. Credential span
parsing is shared by Hook redaction and persistence rejection, and covers direct quoted JSON,
escaped JSON strings, JSON `\uXXXX`/`\xXX` spellings of credential-key characters, nested
object/array values, bracket assignments, and dotted assignments. The entire canonical
`event_json` is scanned, not only `message_template`. Nested containers, forged safety claims,
unknown fields, and secret-bearing values therefore cannot bypass Hook sanitization or the
persistence boundary.

## Evidence model

Use these evidence labels:

- `observed`: temporal association only
- `correlated`: repeated across independent incidents
- `reproduced`: controlled return to the failing condition and isolated reapplication
- `validated`: positive and negative evals passed within a documented scope

Count independent retry groups and sessions, not raw attempts. Preserve failures, partial outcomes, missing outcomes, and counterexamples alongside successes.

An authenticated later explicit success matching the same session, repository, tool, and
operation may be stored as an `indirect` recovery with causal strength `none`. Direct unsigned
recovery claims never create markers. Matching is independent of spool filenames and arrival
batches: within a scope, observations are ordered by normalized `observed_at`, failures precede
recoveries at equal timestamps, and stable identifiers break remaining ties. Each recovery takes
the latest unmatched eligible failure at or before its timestamp. Pending recovery markers are
reconciled again when failures arrive, but never attach to failures observed later than the
recovery. Unsigned events are ineligible unless a human accepted-review overlay explicitly admits
them. The same review predicate governs patterns and recovery matching: quarantined or
non-actionable overlays exclude even authenticated events, while an accepted overlay restores
eligibility and deterministically rematches retained recovery markers. A recovery shows temporal
association, not which change caused it. Manual failure, partial, or unknown intervention outcomes
remain counterevidence but do not consume the authenticated recovery marker; a later eligible
success may still create one automatic recovery. A manual success consumes that opportunity, and
the marker constraint permits at most one automatic recovery outcome.

Patterns use exact repository, tool, operation, and normalized error scope. Rebuild and provenance
linking apply the same eligibility predicate, and `incident_count` must equal the number of linked
events. Advice is generated as a bounded cache without message templates or executable content.

## Learning cases

A case stores a sanitized title, category, opaque scope, root-cause class, remediation class,
verification status, HMAC-pseudonymized opaque evidence references, target fingerprint, and
lifecycle status. The evidence scheme remains readable, but the caller token never does. It
does not copy event bodies, commands, prompts, responses, or causal narratives from the ledger.

## Export and purge

A full export opens a read-only transaction, verifies integrity, the exact current schema, both
privacy component states, and the aggregate readiness marker, then includes every ledger table.
It writes a mode-600 same-directory temporary file, flushes and fsyncs it, and atomically replaces
the requested destination. Any privacy, read, validation, serialization, flush, fsync, or
replacement failure is fail closed and leaves an existing destination unchanged.

Purge first atomically disables collection, then acquires the drain, privacy, and identity locks.
It removes the database and sidecars, identity key, advice cache, spool and rejected bodies, and
known temporary files. It reports success only after a postcondition scan finds no residue other
than the disabled marker and lock control files; a busy lock, removal error, or remaining artifact
is a partial failure and collection stays disabled.

## Promotion gates

Do not promote knowledge when any of these are true:

- a secret or cross-repository leak is possible
- advice could directly cause escalation, deletion, publication, or external transmission
- the collector can block the original task
- evidence is only a raw count or naive success ratio
- the scope, tool version, counterexamples, or expiration condition is missing
- an unreviewed draft would be placed in a discovered Skill root

Use occurrence thresholds only to start review. Require human approval for every durable publication.
