# closeout_v1

```yaml
job_id: opaque_id
attempt_id: opaque_id
plan_digest: sha256:opaque
work_status: planning | implementing | verifying | completed | attention_required | blocked
stage_statuses:
  plan: pending | active | completed | failed | blocked | waiting_human | unknown
  review: pending | active | completed | failed | blocked | waiting_human | unknown
  implement: pending | active | completed | failed | blocked | waiting_human | unknown
  verify: pending | active | completed | failed | blocked | waiting_human | unknown
  report: pending | active | completed | failed | blocked | waiting_human | unknown
completion_state: complete | partial | blocked | failed | unknown
decision_support:
  materials:
    - id: opaque_id
      label: bounded string <=160
      evidence_refs: [evidence:opaque]
      confidence: high | medium | low | unknown
  hypotheses:
    - id: opaque_id
      statement: bounded string <=240
      status: untested | supported | refuted | unknown
      evidence_refs: [evidence:opaque]
      falsifier_ref: opaque_ref | null
  recommendations:
    - id: opaque_id
      label: bounded string <=160
      rationale_ref: opaque_ref
      evidence_refs: [evidence:opaque]
      status: proposed | approved | rejected | deferred
      approval_evidence_ref: evidence:opaque | null
  next_actions:
    - ref: opaque_ref
      label: bounded string <=160
      actor: ai | human | external
      deadline: bounded timestamp | null
      done_when: bounded string <=160
      dependency_refs: [opaque_ref]
      status: ready | blocked | waiting_approval
completed_items:
  - id: opaque_id
    label: bounded string
    evidence_refs: [evidence:opaque]
changes:
  - path: opaque_ref
    kind: created | modified | deleted | external-state
    status: observed | not-observed | metadata-only
    evidence_ref: evidence:opaque | null
checks:
  - name: bounded string
    status: passed | failed | unavailable | not-run
    evidence_ref: evidence:opaque | null
caveats:
  - severity: critical | high | medium | low
    ref: opaque_ref
pending_user_actions:
  - id: opaque_id
    reason: bounded string
    deadline: bounded timestamp | null
    prepared_materials: [opaque_ref]
    done_when: bounded string
    status: approval_requested | approved | declined | pending
next_action: null | {ref: opaque_ref, label: bounded string}
approval_queue_refs: [opaque_ref]
unresolved_refs: [opaque_ref]
generated_at: canonical timestamp
```

All strings and arrays are length-bounded. Only the listed keys and enums are accepted; unknown keys, bodies, paths, prompts, responses, and tool output cause rejection. `changes` always carries `status` and `evidence_ref`; `metadata-only` is the sole exception to evidence-bearing changes and must not claim an observed mutation. `plan_digest` and `attempt_id` must match the active job. A stale-only handoff maps to `attention_required`; a digest/attempt mismatch is rejected or maps to `blocked`. `waiting_human` or `approval_requested` maps to `blocked`; `unknown` maps to `blocked`. None may be reported as `completed` without existing completion evidence. The machine enum is rendered as Japanese: 計画中/実装中/検証中/完了/要対応/ブロック. `next_action` is at most one local-resolution opaque ref with a short label. `approval_requested` is never treated as `approved`.
