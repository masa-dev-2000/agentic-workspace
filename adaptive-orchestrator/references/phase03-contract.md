# Phase 0-3 execution contract

This contract adds a backward-compatible control boundary. It does not move,
delete, disable, or cut over any existing Skill or wrapper.

## Responsibility ownership

| Entry | Owns | Does not own |
| --- | --- | --- |
| `project-control` | goals, tasks, project ledger | run dispatch, domain work |
| `execution-engine` | runs, attempts, dispatch decisions | project ledger truth, domain output |
| `domain` | the assigned domain operation | routing, approval, project state |

The Phase 0-3 adapter exposes only `observe-only` and `plan-only` modes. Both
modes reject dispatch, ledger writes, approval, memory writes, and external
side effects. Append-only telemetry is the only permitted persistence.

## Common envelope

Every observed or planned action uses exactly these fields:

```yaml
project_id: string
task_id: string
run_id: string
attempt_id: string
trace_id: string
origin: string
canonical_entry: string
registry_revision: string
ledger_revision: string
policy_revision: string
authority: observe|recommend|write_local|execute_reversible|execute_external|approval_required
side_effect_mode: observe-only|plan-only|telemetry-only
idempotency_key: string
parent_event_id: string|null
created_at: string
```

Unknown fields are rejected. `idempotency_key` is used for append-only
telemetry de-duplication; it does not grant execution authority.

## Registry overlay

`references/registry-orchestration.yaml` is an optional overlay. The existing
`skill-registry.yaml` remains the source of legacy Skill registration. An entry
is valid only when it exists in either source. Unknown direct entry requests
are rejected by the validator; legacy registered entries remain compatible and
are reported as legacy rather than silently treated as the new Facade.

## Phase boundary

`observe-only` may inspect and emit metadata. `plan-only` may produce a plan
artifact but may not execute it. Neither mode may perform dispatch, state
mutation, approval, memory persistence, or external operations.

Phase 4-6 are deliberately out of scope: single-writer/CAS state transitions,
approval fingerprints and retry budgets, replay/canary comparison, cutover,
rollback, and kill-switch verification.
