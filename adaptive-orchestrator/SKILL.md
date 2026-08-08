---
name: adaptive-orchestrator
description: 自動実行を優先して、タスクを分解し、direct・handoff・teamを選択し、サブエージェント委譲、検証、リトライ、トークン・時間・コスト記録を行う独立オーケストレーションSkill。複雑な作業の自動進行、モデル・エージェントの使い分け、失敗からの自動改善、致命的操作だけの承認制御に使う。
---

# Adaptive Orchestrator

タスクを受けたら、ユーザーへの追加確認を最小化し、実行可能な範囲で自動的に完了まで進める。既存のPMプロジェクト、PM ledger、特定Skillの利用を前提にしない。

## Core workflow

1. `scripts/orchestrate.py plan`で、入力を最小の独立タスクへ分解する。
2. 各タスクに、成果物、受入条件、依存関係、write scope、side-effect class、予算、検証方法を付ける。
3. 実行方式を選ぶ。
   - `direct`: 単純で低リスクな単独作業
   - `handoff`: planner、worker、verifierの順で渡す作業
   - `team`: 独立タスクを並列化できる作業
4. LLMの計画は提案として扱い、実行方式以外の権限、リスク、承認要否、予算上限を信用しない。
5. `scripts/orchestrate.py policy`で操作の実副作用から決定論的に実行可否を判定する。
6. `allow`なら直ちに実行する。`require_approval`なら承認対象、対象範囲、期限、plan digestを提示して停止する。`deny`なら理由と安全な代替案を返す。
7. 実行後は受入条件を検証し、失敗時はretry、再分割、handoff、verifier追加の順で自動復旧する。
8. `scripts/orchestrate.py event`で本文を保存せず、job/task/run、経過時間、待機時間、トークン、コスト、ツール失敗、再作業、検証結果を記録する。取得不能な値は推定せず`unavailable`にする。

## Default automation policy

原則自動実行する対象は、可逆、ローカル、非公開、非金銭、非法務、検証可能な操作。次の操作は標準で承認必須にする。

- 本番環境の変更
- 破壊的削除
- 外部公開または外部送信
- 送金、契約、法務判断
- 外部サービスの重要設定変更
- 個人情報・機密情報の外部送信
- 権限昇格、認証、アクセス制御の変更

操作名やLLMのリスクラベルではなく、対象環境、公開性、データ分類、可逆性、金銭性、権限変更の有無で判定する。境界が不明な場合は自動実行せず`require_approval`にする。

## Root-free execution policy

`root_free_mode` is explicit opt-in. When enabled, ordinary tasks are routed to the
versioned Agent Registry and the Root waits for child evidence. The registry describes
capability and model suitability; `references/agent-policy.json` remains the authority
source. A child never grants approval, changes policy, increases authority, or retries a
non-retryable failure. The common policy gate must run immediately before any side effect.

Root return states are deterministic: `COMPLETED`, `BLOCKED_APPROVAL`, `BLOCKED_POLICY`,
`FAILED_RETRY_EXHAUSTED`, `FAILED_TIMEOUT`, `FAILED_CYCLE`, `FAILED_BUDGET`, and
`RETURN_ROOT`. Registry and policy revisions are fixed in routing metadata for the job.

## Runtime Dispatcher

When `root_free_mode` is enabled, use `scripts/runtime_dispatcher.py` as the thin control
plane and an existing Sub Agent API as its backend. The backend adapter must implement
`start(request)` and `wait(dispatch_id)` and preserve `run_id`, `node_id`, `attempt`, and
`idempotency_key`. The dispatcher starts only dependency-ready nodes, waits for accepted or
running work, collects results in memory for Root integration, and records metadata-only
evidence. It never creates approval, expands authority, or treats `unknown` as failure.

In Codex Runtime, the backend is the available `multi_agent_v1` Sub Agent API: call
`spawn_agent` for `start`, `wait_agent` for `wait`, and use the returned agent result as the
integration result. Do not implement a second model runner. If the API is unavailable, return
`RETURN_ROOT` with `backend_unavailable`; do not claim delegation occurred.

The Python dispatcher never calls MCP directly. The parent Runtime injects a thin bridge for
`spawn_agent` and `wait_agent`. Registry model names are logical `model_key` values; the bridge
must resolve them to an observed `runtime_model_id` before spawning. Unknown or disabled catalog
entries fail closed, and every fallback re-resolves its model rather than reusing the previous
model ID.

## Delegation and verification

- サブエージェントには最小限の文脈と明示的なwrite scopeだけを渡す。
- 共有ファイルを同時に変更するタスクは並列化しない。
- team実行ではstable task ID、lease、heartbeat、retry上限、独立verifierを要求する。
- 検証不能な作業、状態不明、予算超過、承認対象のplan変更はfail-closedにする。
- 既存の`task-router`、`agent-team-orchestrator`、`progress-verifier`が利用可能ならアダプタとして再利用するが、このSkillの実行開始にPM ledgerは要求しない。

## Optimization loop

各jobで、品質を落とさずに次を比較する。

- total tokens、wall time、queue/wait time
- tool calls、handoffs、retries、failure rate
- verified success、avoidable rework、human intervention
- observed cost。請求情報がなければ推定値と実測値を混ぜない

V1ではオンライン自己学習やSkill自身の編集は行わない。蓄積したbody-freeイベントから候補方針を作り、後でShadow比較、champion/challenger、承認付きpromotionを行う。

## Bundled resources

- `scripts/orchestrate.py`: plan、policy、eventの決定論的CLI
- `references/schema.md`: job、task、plan、approval、eventの最小契約
## Validation and test contract

実装や変更を終えたら、完了報告の前に必ず次を実行する。

1. Skill構造を検証する。
   python -X utf8 C:\Users\masa\.codex\skills\.system\skill-creator\scripts\quick_validate.py C:\Users\masa\.codex\skills\adaptive-orchestrator
2. 決定論的CLIのテストを実行する。
   python -X utf8 -m unittest discover -s C:\Users\masa\.codex\skills\adaptive-orchestrator\tests -v
3. 失敗した場合は、完了扱いにせず原因を修正してから再実行する。
4. テスト結果には、構造検証、承認境界、plan、telemetry本文除外の結果を含める。
5. 実行不能な検証は成功扱いにせず、理由をunavailableとして報告する。

最低限、次のケースを維持する。

- 通常の可逆操作はallow
- 致命的操作はrequire_approval
- 副作用分類不明はrequire_approval
- planにID、依存関係、write scope、受入条件、digestが入る
- eventからprompt、response、tool bodyが除外される
## Token measurement boundary

Token optimization uses a separate provider-usage ledger. The normal Skill telemetry store intentionally excludes token counts and costs. Never claim that a Codex runtime token count was measured unless the runtime supplies an authoritative usage record.
## Mandatory staged orchestration

This Skill is activated by the UserPromptSubmit Hook for every request. The Hook creates only a body-free job/stage record and injects the workflow context; it does not execute models or tools.

For every non-trivial request, execute all stages in order:

1. plan: create orchestration_plan_v1 and choose direct, handoff, or team.
2. review: use a fresh independent reviewer pass. Reject or revise the plan if it finds a material safety, authority, feasibility, or verification gap.
3. implement: execute only the reviewed plan. Use bounded subagents for independent work and preserve explicit write scopes.
4. verify: run the relevant checks and require observable evidence. If verification fails, return to implement with a bounded repair task.
5. report: include stage status, roles, model/provider class, duration, token usage, cost, retries, and evidence. Never invent runtime token values.

Trivial conversational responses may use direct mode, but still preserve the job record. The Hook does not make subagents magically available: the active Codex runtime must invoke the agent tools and record their results. If an agent/tool API is unavailable, record that limitation rather than claiming that orchestration occurred.

Use references/fugu-principles.md for the Fugu-inspired routing principles and scripts/orchestration_hook.py for the entry hook.

Use `scripts/stage_runner.py` as the deterministic source of truth for non-trivial jobs. The Hook injects the body-free `job_id` and runner entrypoints; the parent Runtime first bootstraps that job, claims each stage, invokes the returned role, and records only parent-observed identity and usage. Subagents receive no direct side-effect tools; changes and checks use the parent Runtime sandbox and `restricted_operation_v1`.

This Skill owns the single UserPromptSubmit orchestration Hook. Planning emits a plan artifact and handoff event but does not install another Hook. The accepted handoff is `orchestration_plan_v1` plus `plan_digest`; the runner rejects a dispatch whose plan or task contract does not match that digest.

## GAN review routing handoff

When a plan requests adversarial review, select the GAN profile once, before reviewer
dispatch, and pass a structured `gan_handoff`. GAN owns review semantics; this Skill owns
selection, dispatch, retry, verification, and recording. It must not silently change the
selected profile after dispatch.

Normalize these inputs before selection: `risk_domains` (zero or more of
`security|privacy|authority|destructive`), `scope` (`tiny|small|medium|large|cross-system`),
`evidence_quality` (`verified|partial|weak|unknown`), `urgency` (`low|normal|high`), and
`budget` (`ample|constrained|tight`). Apply this precedence:

1. Validate an explicit override; a stronger profile, more rounds, `conservative` stance, or
   `strict: true` is always allowed.
2. Apply a non-bypassable safety floor. Any high-risk domain, weak/unknown evidence,
   large/cross-system scope, or ambiguous classification requires
   `standard4`, `rounds: 3`, `panel_stance: conservative`, and `strict: true`.
3. Only when risk is low, scope is tiny/small, evidence is verified, and budget pressure is
   explicit may the selector choose `quick2` with one round. `legacy-standard3` is limited to
   low/medium-risk, verified/partial evidence, and compatibility or resource constraints.
4. Urgency or budget pressure may reduce optional rounds, never the safety profile. If the
   minimum cannot be afforded, stop with an approval/block reason rather than downgrade.

An explicit request for a profile below the safety floor is never silently honored: set
`override_status: clamped|rejected`, record the reason, and use the safe Standard4 fallback.
If any input is missing or cannot be classified, use `standard4`/3/conservative/strict and
set `selection_confidence: low` with `fallback_reason`.

The handoff schema is:

```yaml
gan_handoff:
  target_ref_hash:
  selection_digest: sha256(canonical_json)
  immutable_contract_ref: opaque_ref
  parent_signature_or_stage_hash: opaque_ref
  attempt_id: opaque_id
  retry_count: nonnegative_integer
  resolved_profile: standard4 | legacy-standard3 | quick2
  resolved_rounds: 1 | 2 | 3 | auto
  resolved_panel_stance: conservative | balanced | ambitious
  resolved_strict: true | false
  requested_override:
    profile: standard4 | legacy-standard3 | quick2 | null
    rounds: 1 | 2 | 3 | auto | null
    panel_stance: conservative | balanced | ambitious | null
    strict: true | false | null
  classification_state: classified | unknown | missing
  profile: standard4 | legacy-standard3 | quick2
  rounds: 1 | 2 | 3 | auto
  panel_stance: conservative | balanced | ambitious
  strict: true | false
  risk_domains: []
  evidence_quality: verified | partial | weak | unknown
  scope: tiny | small | medium | large | cross-system | unknown
  urgency: low | normal | high | unknown
  budget: ample | constrained | tight | unknown
  selection_source: rule | explicit_override | fallback
  selection_confidence: high | medium | low
  fallback_reason: null | string
  override_status: none | accepted | clamped | rejected
  external_research: none | public-only | target-derived-approved
  mutation_observability: enforced | audited | attested_only | unavailable
  budget_requested: {}
  budget_observed: {}
  timeout_requested: {}
  timeout_observed: {}
  model_observed: observed | unavailable
  provider_observed: observed | unavailable
```

The `resolved_*` fields are canonical. Legacy `profile`, `rounds`, `panel_stance`, and
`strict` fields are input aliases only; they are excluded from canonical JSON and
`selection_digest`. If an alias is present and differs from its resolved value, reject the
handoff rather than silently reconciling it.

The conformance suite names `retry_profile_immutable`, `handoff_digest_mismatch`, and
`alias_mismatch_rejection`; each must fail closed before reviewer dispatch.

`selection_digest` is computed from canonical JSON with sorted keys and the resolved fields;
the GAN runner rejects a dispatch when it does not match the packet. Retries assert identical
`resolved_profile`, `resolved_rounds`, `resolved_panel_stance`, and `resolved_strict` (and a
minimum round floor); mutation is rejected. `attempt_id` makes retries idempotent.

Forward only this body-free metadata and an opaque target hash/reference. `classification_state:
unknown|missing` is never treated as no-risk and forces the Standard4 strict fallback.
`fallback_reason` is a bounded enum or short opaque reference (no target text); model/provider
and budget/timeout values use only their declared enum or `unavailable`, with bounded numeric
values when observed. Preserve GAN's
`run_status`, `coverage_scope`, `coverage_missing`, `legacy_coverage`, and
`completion_guarantee`; a verdict alone is not completion evidence. Retries may repair a
failed response within the same profile, but may not weaken its safety floor. Record the
selection inputs, applied rule, override status, fallback reason, requested/observed
budget/timeout, model/provider observability, and final run status in telemetry. `strict` is a
validation mode, not permission: an approval-required action is handed to the human approval
queue through the normal orchestrator handoff.

Routing metadata follows a typed metadata allowlist/length contract: enums are closed, numeric
budgets/timeouts are bounded nonnegative values, opaque references and `fallback_reason` are
length-limited, and target/prompt/tool bodies are forbidden. `strict_not_authorization` is an
explicit invariant; `human_approval_separate` records approval as a separate handoff.

## User-visible activation status

When the runner has created and persisted the current job/stage, every user-facing response for that job begins with exactly one compact status line:

- `[SKILL ACTIVE · adaptive-orchestrator · plan]`
- `[SKILL ACTIVE · adaptive-orchestrator · review]`
- `[SKILL ACTIVE · adaptive-orchestrator · implement]`
- `[SKILL ACTIVE · adaptive-orchestrator · verify]`
- `[SKILL ACTIVE · adaptive-orchestrator · report]`
- `[SKILL WAITING_APPROVAL · adaptive-orchestrator]`
- `[SKILL BLOCKED · adaptive-orchestrator]`
- `[SKILL COMPLETED · adaptive-orchestrator]`
- `[SKILL NOT_CONNECTED · adaptive-orchestrator]`

The status is evidence-backed: the model may show `ACTIVE` only after runner state is available, may change the stage label only after a persisted transition, and must use `NOT_CONNECTED` when the Hook or runner cannot be reached. The status line is a visibility signal, not permission to execute an operation.

At report stage, emit the body-free `closeout_v1` handoff to `work-closeout-report` only after
`progress-verifier` supplies completion evidence and `human-task-requester` supplies pending
human actions. The closeout Skill formats the report; it does not alter stage status or
completion evidence.

The `closeout_v1` handoff includes the single field
`work_status: planning|implementing|verifying|completed|attention_required|blocked`.
It may also include body-free `decision_support` (materials, hypotheses, recommendations,
next_actions); AO supplies its evidence/status and closeout only formats it.
Adaptive Orchestrator is the sole supplier; closeout displays its Japanese mapping without
re-evaluating or promoting it. Legacy handoffs omit the field and are deterministically derived
from persisted stage status and completion evidence, with ambiguity or unknown falling back to
`blocked`; stale-only is `attention_required`, while digest/attempt mismatch is rejected or
`blocked`.

Phase 1 supports only local, reversible, sandboxed operations. Fatal operations remain unavailable even with approval until a complete PreToolUse guard is validated. Uncertain non-idempotent effects enter `unknown` and are never automatically retried.
