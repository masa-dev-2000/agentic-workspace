---
name: planning
description: 実装・調査・設計の依頼を、目的・成果物・タスク・依存関係・担当・モデル・権限・検証条件まで含む実行可能な計画へ変換する。ユーザーが「計画して」「実装計画」「plan」「要件から進め方を決めて」と依頼したときに使う。実装は行わず、確定した計画をGANレビューとadaptive-orchestratorへ渡せる形で出力する。
---

# Planning

依頼を実行可能な計画へ正規化する。計画は説明文ではなく、後続のレビュー・実行・検証が参照する構造化成果物として扱う。

## Workflow

1. 目的、意思決定、対象範囲、期限、制約、成功条件を抽出する。
2. 現状の事実と未確認事項を分ける。重要な前提は仮説として明示する。
3. 成果物から逆算して、独立タスク、依存関係、順序、並列化可否を定義する。
4. 各タスクに担当role、推奨model/provider、write scope、side-effect class、受入条件、検証方法、失敗時の代替経路を付ける。
5. `references/plan-schema.md` に従う `orchestration_plan_v1` を作る。計画本体のimmutable
   `plan_digest`を生成する。enrichment後は本体digestを再計算・上書きせず、
   `enrichment_digest`を別に生成する。
6. 実装は開始せず、レビュー対象として確定する。

## Routing contract

- 計画確定後は`review_required`を出力する。GANを直接起動せず、Adaptive OrchestratorがGAN dispatchを一本化する。
- AdaptiveがGAN evidenceを受領し、GoまたはConditional Goを確認した後にdirect・handoff・team、担当エージェント、モデル、予算、再試行を最終決定する。
- Hookは計画確定イベントを記録するだけにし、LLM起動や子プロセス生成を行わない。
- Hookの所有者は`adaptive-orchestrator`の単一Hookとし、Planning SkillはHookを追加・起動しない。
- No-Go、未確認の重大前提、検証不能な完了条件がある場合は実装へ進めず、計画の修正点を返す。

## Handoff contract

`review_required`付きの`orchestration_plan_v1`をAdaptiveへ渡し、AdaptiveがGANをdispatchする。GAN evidence受領後、Adaptiveが同じimmutableな`plan_digest`と`gan_review_evidence`、`enrichment_digest`を保持して実行へ進める。計画本文を再解釈して権限を拡大せず、計画変更時は新しいplan digestを生成し、旧承認・旧dispatchを無効化する。handoff順序はPlanning→Adaptive→GAN→executionで固定する。

## Output

最初に短い結論を示し、その後に次を出力する。

- 目的と完了条件
- スコープ内／対象外
- 前提・未確認・リスク
- マイルストーンとタスク表
- 依存関係と並列化方針
- role・model/provider・権限・副作用
- 検証計画と完了証拠
- 人間の承認が必要な地点
- GANレビューへ渡す対象と、判断待ちの論点

## Boundaries

- 計画だけで完了扱いにしない。
- モデル名や権限を理由に承認を省略しない。実際の副作用をpolicyで判定する。
- 不明な期限・コスト・性能を推測で埋めない。`unavailable`または確認項目にする。
- 既存のPM台帳がある場合は、計画の正本を台帳へ保存し、会話だけに残さない。
- 新しいSkillを増やす判断は行わず、必要なら既存Skillとの責務境界を提案する。
