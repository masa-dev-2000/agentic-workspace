---
name: skill-telemetry
description: Record, inspect, diagnose, and summarize local usage telemetry for every Codex Skill, including invocation counts, lifecycle outcomes, duration, tool failures, version fingerprints, and explicit user sentiment. Use when the user asks whether Skills are being logged, which Skills are used, how they perform, whether telemetry is healthy, or what should be improved from accumulated Skill evidence.
---

# Skill Telemetry

Treat telemetry as observational evidence. Do not equate a returned response with task success.

## Inspect

Resolve paths relative to this file and run:

```powershell
python -X utf8 scripts/telemetry_cli.py init
python -X utf8 scripts/telemetry_cli.py drain
python -X utf8 scripts/telemetry_cli.py drain --limit 5000 --max-seconds 30
python -X utf8 scripts/telemetry_cli.py doctor
python -X utf8 scripts/telemetry_cli.py status
python -X utf8 scripts/telemetry_cli.py stats --days 30
python -X utf8 scripts/telemetry_cli.py runs --limit 20
python -X utf8 scripts/telemetry_cli.py evaluation-sample --skill SKILL_NAME --limit 10
python -X utf8 scripts/telemetry_cli.py evidence-list
```

Report explicit and inferred detections separately. Use version fingerprints when comparing
before and after a Skill change. `stats` returns `{freshness, groups}`; each group is separated
by Skill fingerprint, provenance trust, and detection, and feedback is aggregated per run
before run counts are computed.

## Track Skill evolution

Capture an immutable snapshot whenever a Skill contract, instructions, scripts, tests, or
registry entry materially changes:

```powershell
python -X utf8 scripts/skill_history.py snapshot --skill SKILL_NAME
python -X utf8 scripts/skill_history.py history --skill SKILL_NAME
```

Backfill older milestones from retained evaluation artifacts when exact historical Skill
content is unavailable:

```powershell
python -X utf8 scripts/skill_history.py backfill --skill SKILL_NAME `
  --evidence-root PATH_TO_SKILL_EVAL_HISTORY
```

Snapshots record the observed Skill version, contract fingerprints, and a file hash manifest.
Backfilled events record only evidence hashes and counts, mark provenance `inferred`, and state
that the historical Skill body and causality are unknown. Never upgrade reconstructed history
to observed fact. Run history capture after validation so a recorded version points to tested
content; activation still requires the separate exact human-approval workflow.

Render the current lineage and canonical capability dependencies into the local interactive
atlas when the user wants a roadmap or network view:

```powershell
python -X utf8 scripts/render_skill_atlas.py --output PATH/index.html
```

The atlas reads the canonical registry and lineage ledger at generation time. It shows the
history roadmap and Skill dependency network as derived views, never as a second source of
truth.

For outcome review, inspect the selected case's original local evidence without copying it into
telemetry. First record each completion, authority, and domain-verdict observation against the
same session and turn as the run. Keep the returned `evidence_id` values, then evaluate with
linked `evidence:EVIDENCE_ID` references:

```powershell
python -X utf8 scripts/telemetry_cli.py evidence --skill SKILL_NAME `
  --session SESSION_ID --turn TURN_ID --class test --result passed `
  --subject opaque-local-test-reference
python -X utf8 scripts/telemetry_cli.py evidence --skill SKILL_NAME `
  --session SESSION_ID --turn TURN_ID --class authority --result passed `
  --subject opaque-local-authority-reference
python -X utf8 scripts/telemetry_cli.py evidence --skill SKILL_NAME `
  --session SESSION_ID --turn TURN_ID --class domain-verdict --result passed `
  --subject opaque-local-verdict-reference
python -X utf8 scripts/telemetry_cli.py evaluate RUN_ID --outcome verified-success `
  --outcome-achieved 2 --completion-evidence 2 --authority-safety 2 `
  --avoidable-rework 1 --efficient-recoverable 1 `
  --evidence-class test --evidence-class domain-verdict `
  --evidence-class authority `
  --evidence-ref evidence:TEST_EVIDENCE_ID `
  --evidence-ref evidence:AUTHORITY_EVIDENCE_ID `
  --evidence-ref evidence:DOMAIN_VERDICT_EVIDENCE_ID `
  --evaluator codex
```

Use `unverified` with no scores when the available evidence cannot establish the outcome.
`verified-success` requires trusted, passed completion, explicit-manual domain-verdict, and
explicit-manual authority evidence that all exist in the local evidence ledger and are linked
only to the exact evaluated run. The run itself must be `returned` through `stop` or
`manual-returned` with an exact duration. Every supplied reference must use the
`evidence:EVIDENCE_ID` scheme and resolve to one trusted, passed evidence row with exactly one
total run link; failed, legacy, invented, unlinked, fan-out, cross-run, and non-evidence
references fail closed. Positive feedback is supplementary and never substitutes for those
three classes.
Evidence references must use an allowed `scheme:opaque-token` form. Storage retains the scheme
but replaces every token, including a shape-valid token, with a domain HMAC. Evaluator and
rubric identities are fixed CLI choices; free-form labels are rejected.

## Record manually

Automatic hooks are preferred. For workflows outside supported hooks:

```powershell
python -X utf8 scripts/telemetry_cli.py start SKILL_NAME --session SESSION_ID --turn TURN_ID
python -X utf8 scripts/telemetry_cli.py finish RUN_ID --status returned
python -X utf8 scripts/telemetry_cli.py feedback RUN_ID --sentiment positive `
  --feeling explicit-approval
```

Valid finish states are `returned`, `failed`, and `interrupted`. A manual rating is optional.
Prefer session/turn-bound manual runs so the normal Stop hook can close them.
Manual Skill names must be lowercase canonical slugs. `--model` accepts only the coarse
classes `openai`, `anthropic`, `google`, `local`, `test`, or `unknown` (or the empty default).
Feeling classes are `explicit-approval`, `explicit-complaint-or-correction`, and
`explicit-mixed-reaction`; arbitrary text is rejected.

For supported work outside PostToolUse auto-classification, attach privacy-safe evidence:

```powershell
python -X utf8 scripts/telemetry_cli.py evidence --skill SKILL_NAME `
  --session SESSION_ID --turn TURN_ID --class test --result passed `
  --subject opaque-local-reference
python -X utf8 scripts/telemetry_cli.py evidence --skill SKILL_NAME `
  --session SESSION_ID --turn TURN_ID --class domain-verdict --result passed `
  --subject opaque-local-verdict-reference
```

`--subject` is HMAC-pseudonymized before storage. Do not pass prompt, response, command, or
tool-output bodies. Automatic evidence links only when exactly one candidate run exists;
an explicit Skill key narrows candidates but never permits fan-out when the same Skill has
multiple same-turn runs. Multi-Skill or otherwise ambiguous evidence is retained unlinked.
Screenshot acquisition alone records
`browser-qa: ambiguous`, not a passed QA verdict.

To recover already orphaned rows without deleting history:

```powershell
python -X utf8 scripts/telemetry_cli.py reconcile
```

Hooks only append one privacy-safe envelope to the local spool. They never open SQLite.
Run `drain` before an inspection that must include the newest Hook events. `reconcile`
also drains first. Initialization, start, finish, feedback, evidence, evaluation, status, and
configuration never drain implicitly; only explicit `drain` or `reconcile` processes unrelated
pending envelopes. Drain validates an exact body-free envelope allowlist and quarantines
malformed, oversized, or nonconforming records without issuing a receipt. Turn terminal
markers make Stop and superseding prompt handling independent of Hook arrival order. Signed
spool v2 envelopes use a canonical HMAC over every persisted field; unsigned, legacy-version,
or modified envelopes are replaced by body-free rejection metadata. If the stable key is not
available, the Hook drops the event rather than writing an unverifiable envelope.
Before signing, a referenced Skill path must be authorized by the canonical local registry,
the exact system/agents Skill layout, or an installed Plugin root plus its manifest `skills`
entry. Cached Plugins must have the exact
`plugins/cache/<source>/<plugin>/<version>/skills/<skill>/SKILL.md` layout, with manifest name
and version equal to those directories. Identity comes from that registry, trusted directory,
and manifest—not Skill frontmatter. An unregistered temporary, backup-depth, or lookalike
`SKILL.md` is omitted from the envelope. Repeated references to the same resolved path are
deduplicated before authorization.

The default drain budget is one second. Use the bounded maintenance form only for a known local
backlog; the CLI caps it at 5,000 envelopes and 30 seconds, and any uncommitted record remains in
spool without a receipt when the budget expires.

Schema v6 adds `end_reason` and `duration_quality` (`pending`, `exact`, `bounded`, or `unknown`)
and preserves v5 trusted IDs during its schema-only migration. Schema v5 marks validated manual
and signed-spool run/evidence rows as `trusted`. Pre-v5 rows
remain counted as `legacy-unverified`. Migration redacts enumerated free-text fields and
domain-HMACs every legacy identifier, digest-shaped value, and evidence-reference token even
when it already has a valid-looking shape; outcome sampling uses only trusted provenance.
Privacy repair protocol v3 commits transformed rows as `pending-v3`, requires a successful
transaction-free `wal_checkpoint(TRUNCATE)`, and only then writes final version `3`. An active
reader therefore leaves initialization fail-closed and pending; `status` and `doctor` report
that state read-only. A later initialization retries cleanup without HMACing IDs again.
For a run, the earliest real event time among same-turn Stop and a later-turn prompt determines
status, end time, and duration. A delayed older prompt never interrupts a run that started
after that prompt. New events use canonical UTC microsecond timestamps. Canonical legacy
second-precision events remain readable, but ordering within the same legacy second—or at an
identical microsecond—is unproven, so feedback attachment and lifecycle reconciliation hold
rather than guessing. Proven-orphan recovery uses the later run's start as the bounded end-time
evidence; stale-timeout recovery is also bounded rather than exact.

## Maintain hooks

```powershell
python -X utf8 scripts/configure_hooks.py status
python -X utf8 scripts/configure_hooks.py install
```

Hook collection must fail open, finish quickly, preserve unrelated hooks, store no prompt or
assistant-response body, and never block the original task. `install` provisions the stable
pseudonym key and database before enabling Hooks. Read
[schema.md](references/schema.md) before changing storage, classification, or retention.
