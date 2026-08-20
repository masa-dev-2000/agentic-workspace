---
status: candidate
observed_at: 2026-08-20
source: Anthropic Claude Code 2.1.238 self-hosted runner changes
---

# Graceful runner drain pattern

## why_relevant

長時間Agentをself-hosted runnerで動かす場合、deploy・再起動・SIGTERMで処理を即切断すると作業状態や外部操作が中途半端になる。既存sessionを一定時間継続し、残作業をpark/checkpointしてから終了するdrain方式はprovider-neutralなRunner運用へ転用価値が高い。

## portable_pattern

- 新規task受付を止めてrunnerをdraining状態にする
- 実行中sessionは設定したgrace period内だけ継続する
- grace period超過taskはkillではなくpark/checkpointを優先する
- parkしたtaskは別runnerで安全にresumeできる状態を持つ
- handoff前にpost-session hook / cleanup完了を保証する
- health pollの一時失敗だけでhealthy sessionを別runnerへ奪わせない
- runner state transitionを `active -> draining -> parked/complete -> stopped` と明示する
- shutdown / handoff / resumeをauditする

## possible_component

`agents/runner/` + `hooks/lifecycle/`

`RunnerLifecycle + SessionCheckpoint + HandoffPolicy`

## next_action

既存runner実装の有無を確認し、最小のlifecycle state machineとSIGTERM handlerを設計する。PoCではprocess-level taskを対象に、graceful drainとcheckpoint metadataのみ実装する。
