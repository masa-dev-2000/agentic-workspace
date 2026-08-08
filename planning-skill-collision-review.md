# Planning Skill 共有・競合判定メモ

更新日: 2026-08-03

## 今回の結論

独立した `planning` Skill を作成した。標準Plan modeは使わず、明示的に計画作成を依頼したときだけ使う。

## 作成物

- Skill: `C:\\Users\\masa\\.codex\\skills\\planning\\SKILL.md`
- UI metadata: `planning\\agents\\openai.yaml`
- Schema reference: `planning\\references\\plan-schema.md`
- Registry entry: `skill-registry.yaml` の `key: planning`

## 責務

ユーザー意図を、`orchestration_plan_v1` として実行可能な計画へ正規化する。

含めるもの:

- ゴール、成果物、成功条件
- スコープ、前提、未確認事項
- タスク、依存関係、並列化
- role、model/provider、write scope、副作用
- 承認地点、検証方法、完了証拠
- GANレビューへの引き渡し情報

実装、GAN判定、権限付与、会話だけへの保存は行わない。

## 既存Skillとの境界

| Skill | 責務 | Planningとの関係 |
|---|---|---|
| planning | 実行可能な計画の作成 | 起点 |
| gan | 計画・提案の敵対的レビュー | 計画確定後に起動 |
| adaptive-orchestrator | 実行方式・担当・モデル・再試行 | GAN通過後に起動 |
| progress-verifier | 成果と外部状態の検証 | 実行後 |
| project-orchestrator | PM台帳に基づくプロジェクト運営 | 必要時に上位司令塔 |

## 競合判定ポイント

別セッションに類似Skillがある場合、次を比較する。

1. Skill名とpath
2. frontmatterのdescriptionと起動条件
3. 計画の正本（台帳、ファイル、会話）
4. `orchestration_plan_v1`互換性
5. GAN・Orchestratorへのhandoff契約
6. registryのcapabilityとversion
7. HookやRuntimeの二重起動

同じ責務なら新しい方を増やさず、片方を統合・廃止する。

## 検証結果

- `quick_validate.py planning`: PASS
- registry validator: Planning固有エラーなし
- registry全体: 2026-08-03時点で PASS。未登録Skill 7件を登録し、explicit-only metadataを追加

## 重要な状態変更

`skill-maturity-gate.json` は、Registry validatorの2026-08-03再検証結果を追加した上で`unfrozen`を維持している。

## 次の判断

- `planning`と`adaptive-orchestrator`は責務を分離したまま維持する。
- Planningの`orchestration_plan_v1`をGAN経由でAdaptiveへ渡すhandoffを正本契約とする。
- HookはAdaptiveの単一Hookとし、PlanningはHookを追加しない。
