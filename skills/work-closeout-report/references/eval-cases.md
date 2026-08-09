# Closeout report evaluation cases

1. Valid `closeout_v1` with matching `plan_digest`/`attempt_id` produces one status line followed by exactly four Japanese blocks in order.
2. Missing evidence for a claimed completion maps to `unknown` or `blocked`; it never produces `完了` as verified.
3. Stale or mismatched digest/attempt is rejected before formatting.
4. `waiting_human` maps to blocked and `approval_requested` remains distinct from `approved`.
5. Unknown fields, bodies, overlong strings, invalid enums, or failed raw scan are rejected/fail-closed.
6. A retry with a changed attempt/profile context is rejected; a valid same-attempt report is idempotent.
7. `work_status` accepts only `planning|implementing|verifying|completed|attention_required|blocked`; closeout never promotes `partial` or `unknown` to `completed`.
8. `waiting_human`/`approval_requested` renders ブロック; stale-only renders 要対応; digest/attempt mismatch is rejected or renders ブロック; old handoffs without `work_status` use deterministic derivation and unknown falls back to ブロック.
9. The status line uses Japanese labels only: 計画中/実装中/検証中/完了/要対応/ブロック; machine enums never appear in the user-facing line.
10. `decision_support` arrays are capped at 3 and reject unknown keys/bodies; materials/hypotheses lack of evidence stays unknown; recommendation proposed is distinct from approved and approved requires approval evidence.
11. `actor: human|ai|external` maps to あなたに必要/次の自動処理/外部依頼; unknown actor is rejected or blocked. Legacy `next_action` is imported deterministically and exactly four blocks remain.
