# Phase 4-6 Evidence Pack Contract

This pack measures existing implementation boundaries. It is not a second
state machine, dispatcher, simulator, monkeypatch, or socket patch.

## Evidence lanes

- Phase 4 calls the real `stage_runner.claim`, `stage_runner.record_result`,
  `stage_runner.recover`, and SQLite integrity check against an explicitly
  temporary database. Multiple spawned processes claim the same job with a
  barrier. Exactly one claim, zero dispatch duplication, zero stale-result
  acceptance, and `integrity_check=ok` are required.
- Phase 3 calls the real `Phase03Boundary` and `registry_validator` boundaries.
  Mutation rejection, telemetry-only acceptance, caller/authority validation,
  unknown-field rejection, and body-free telemetry rejection are separate
  evidence cases.
- Phase 4 approval/retry, Phase 5 replay/shadow, and Phase 6 facade/canary/
  cutover/rollback use the deterministic local runtime contract in
  `scripts/phase46_runtime.py`. These checks do not claim Codex approval or
  network observability; those remain `NOT_OBSERVABLE` or `NOT_RUN`.

## Case schema

Every case contains `run_id`, `case_id`, `phase`, `test_boundary`, fixture and
source/environment/database digests, runtime versions, process and iteration
configuration, worker/owner, lease and revision observations, timing and
barrier data, expected/actual/observed events, process exit codes and child
PIDs, network/global-write observations, cleanup status, classification,
failure reason, and evidence references.

Classifications are strict:

- `PASS`: the executed condition passed with evidence.
- `FAIL`: the condition ran and failed.
- `NOT_RUN`: the current source does not implement the boundary.
- `NOT_OBSERVABLE`: the boundary cannot be proven without prohibited
  instrumentation or an unavailable injection point.

`NOT_RUN` and `NOT_OBSERVABLE` never count as `PASS`. Evidence Pack status and
Phase 4/5/6 readiness are reported separately.

## Persistence and safety

The runner receives `--db <temporary path>` for every Phase 4 operation. The
Evidence Pack writes JSON only when `--output` is explicitly provided. Source,
Registry, existing Phase 0-3 files, and existing tests are hash-checked before
and after execution. No global runner database, Memory backend, network, or
external state is used by the pack.
