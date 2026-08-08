# Adaptive Orchestrator shared status

更新日: 2026-08-03

この文書は、別セッションで作られた類似実装との競合を判断するための共有メモです。本文、会話ログ、ツール入出力は保存していません。

## 結論

現在の実装は、Codexの親RuntimeがAgent Teamを起動し、`stage_runner`へ結果を返す制御面です。`stage_runner`自身はモデルやツールを起動しません。

実装済みの正常系は次のとおりです。

`UserPromptSubmit Hook → Parent Codex Runtime → bootstrap → stage_runner → PLAN → REVIEW → IMPLEMENT / Agent Team → VERIFY → REPORT → completed`

## 現在の正本

| 項目 | 現在の正本 |
|---|---|
| Skill | `adaptive-orchestrator/SKILL.md` |
| 状態機械 | `adaptive-orchestrator/scripts/stage_runner.py` |
| Hook | `adaptive-orchestrator/scripts/orchestration_hook.py` |
| 契約 | `adaptive-orchestrator/references/schema.md` |
| Workflow図 | `adaptive-orchestrator/references/workflow.md` |
| テスト | `adaptive-orchestrator/tests/test_orchestrate.py` と `test_stage_runner.py` |
| SQLite | `adaptive-orchestrator/scripts/orchestration.sqlite3` |

## 実装済み範囲

- body-free job作成と`job_id`のHook注入
- `bootstrap`、`next`、`claim`、`heartbeat`、`recover`
- PLAN、REVIEW、IMPLEMENT、VERIFY、REPORTの状態遷移
- SQLite WAL、`BEGIN IMMEDIATE`、version条件、lease、active dispatch一意制約
- 一回限りのdispatch capability
- 親Runtime観測のruntime handle、principal、model/provider class、usage source
- reviewer、implementer、verifierのprincipal分離
- artifact pathとSHA-256 digestの検証
- `prepared → applying → unknown/reconciled → committed`の副作用状態
- attemptに依存しないlogical operation ID
- fatal、unknown、workspace外操作、sandbox未証明コマンドの拒否
- `repairable`かつretry上限内のVERIFY失敗だけIMPLEMENTへ復帰
- body-free eventとusage unavailableの記録
- Skill構造検証と15件のテスト成功

## 未実装・明示的な制約

- HookからCodex Agent APIを直接呼び出す処理
- 親Runtimeのagent起動adapterそのもの
- PreToolUseによるfatal操作の承認付き実行
- Codex Runtimeが提供しない会話tokenの実測
- usage履歴を使った自動モデル切替とchampion/challenger promotion

Provider APIがusageを返した場合だけtoken、cost、latencyを記録します。Codex Runtimeのusageが提供されない場合は`unavailable`を維持します。

## 別セッションとの競合判断表

比較対象に同じ項目があれば、先に統合方針を決めてから編集します。

| 比較項目 | 現在の実装 | 競合時の判断 |
|---|---|---|
| Skill名 | `adaptive-orchestrator` | 同名Skillは1つへ統合 |
| Hook入口 | `orchestration_hook.py` | job作成とcontext注入を重複させない |
| 状態DB | `orchestration.sqlite3` | DB正本を1つにする。別DBへ分岐しない |
| 状態遷移 | `stage_runner.py` | LLMの自己申告ではなくrunnerを優先 |
| Agent起動者 | Parent Codex Runtime | stage runnerやHookへ起動責務を移さない |
| 必須stage | PLAN / REVIEW / IMPLEMENT / VERIFY / REPORT | stage名と順序を統一 |
| fatal policy | Phase 1はblocked | 承認だけで実行可能に戻さない |
| usage | runtime/provider/unavailable | 推測値を追加しない |
| reviewer独立性 | distinct principal必須 | 自己申告だけの独立性は採用しない |
| artifact | SHA-256 digest | mutable pathだけの証跡は採用しない |
| operation retry | reconcile後のみ | attempt番号付きidempotency keyへ戻さない |

## 統合前に確認する差分

- 別セッションが同じSkill名・Hook設定・SQLiteを変更していないか
- 別実装がAgent起動をHookやPython subprocessへ移していないか
- fatal操作を「承認済みなら実行」としていないか
- tokenを文字数・応答長・モデル名から推定していないか
- stageの完了を報告文だけで判定していないか
- `stage_runner`と別の状態管理台帳を並行していないか

## 実証タスクの記録

今回の実証は「Adaptive OrchestratorのWorkflow図を設計・レビュー・検証する」タスクで実施しました。

- Planner: Mermaid構成と受入条件を作成
- Reviewer: Parent Runtime境界、fatal policy、usage表現、VERIFY復帰条件をレビュー
- Implementer: rootがレビュー済み内容を`references/workflow.md`へ反映
- Verifier: 図とrunnerを照合し、VERIFY failure classificationの不一致を検出
- Repair: runnerへ`failure_class`判定とfatal blocked永続化を追加
- 結果: Skill検証成功、テスト15件成功

実測tokenはRuntimeから取得できないため記録していません。

## 2026-08-03 integration update

- Planning registry capability: `plan.execution.create`
- Adaptive registry capability: `execution.orchestrate.adaptive`
- Seven previously unregistered local Skills are now registered.
- Planning-to-Adaptive handoff is `orchestration_plan_v1` plus `plan_digest` after GAN approval.
- Adaptive owns the single orchestration Hook; Planning does not install a second Hook.
- Canonical Registry validator: `valid: true`, 56 registered and 56 discovered Skills.
- Remaining validator output is warning-only and belongs to pre-existing PM/clone planned components or metadata.
- Planning structure validation: passed.
- Adaptive tests after schema and handoff changes: 15 passed.
