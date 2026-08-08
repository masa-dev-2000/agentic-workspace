# Adaptive Orchestrator schemas

All identifiers are opaque and globally unique within the local runtime. Payload bodies, prompts, responses, tool arguments, and tool outputs are not telemetry fields.

## orchestration_plan_v1

Required: job_id, session_id, goal, execution_mode, tasks, budget, verification, fallback, approval_requirement, plan_digest.

`orchestration_plan_v1` remains the default for backward compatibility. An opt-in `selection_audit` object produces `orchestration_plan_v2` and contains only bounded revisions, intent category, and canonical planner candidate keys.

Each task requires task_id, acceptance_criteria, dependencies, write_scope, side_effect_classes, and retry_limit.

## policy_decision_v1

decision is one of allow, require_approval, or deny. Missing or unknown side-effect classification is require_approval, never allow. A policy decision is valid only for its exact plan_digest.

## approval_v1

An approval binds job_id, plan_digest, action IDs, resource scope, environment, approver, expiry, and a single-use nonce. Replanning, expiry, scope change, or nonce reuse invalidates approval.

## orchestration_event_v1

Events contain lifecycle and aggregate metrics only: identifiers, coarse model class, timings, token counts, cost, tool/retry/handoff counts, status, rework, human intervention, quality, and opaque evidence references. Unavailable measurements use unavailable; they are never inferred from text.

## selection_audit_v1

Selection audits are body-free and shadow-only. Classification precedence is `selected` -> `not_observable` -> `candidate_signal`/`not_comparable` -> `missed_candidate`. Missing execution observation never proves omission. Session cumulative token counters are not valid comparison metrics.

## stage runner v2

The deterministic sequence is `planning -> reviewing -> implementing -> verifying -> reporting -> completed`; control states are `retryable`, `waiting_approval`, `blocked`, `unknown`, `cancelled`, and `failed`.

`runtime_action_v1` binds job, stage, role, attempt, plan digest, immutable input artifact digests, context policy, write scope, acceptance criteria, and a single-use opaque capability. Result identity and usage are parent-runtime observations. Claims use `BEGIN IMMEDIATE`, conditional versions, database time, and one active dispatch per stage.

`restricted_operation_v1` uses an attempt-independent operation ID and `prepared -> applying -> reconciled -> committed`. Uncertain effects become `unknown` and cannot retry without reconciliation. Fatal operations are unavailable until PreToolUse enforcement is validated. Artifacts store canonical paths and SHA-256 digests, never prompt, response, or tool bodies.
