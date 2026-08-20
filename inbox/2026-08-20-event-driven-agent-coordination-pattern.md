---
status: candidate
observed_at: 2026-08-20
source:
  - Anthropic Claude Code 2.1.236 cross-session SendMessage / goal behavior
  - Cursor Cloud Agents and Cursor Harness Improvements (2026-08-19)
---

# Event-driven agent coordination pattern

## why_relevant

複数Agentや長時間Agentを協調させるとき、定期pollingやsilent dropに依存すると無駄な計算と見落としが増える。Claude Codeのone-shot通知/backpressureと、CursorのPR・Slack等のevent subscriptionを組み合わせると、provider-neutralな常駐Agent制御へ抽象化できる。

## portable_pattern

- Event source subscription: PR / thread / schedule / task状態などを購読する
- Event-driven wakeup: idle / changed / completed / failed等でAgentを再開し、pollingを減らす
- Durable goal: 一度のturnではなく、完了条件を満たすまでgoalを保持する
- One-shot notification: 必要な通知は一度だけ送り、重複wake-upを避ける
- Correlation: session / task / request / event-source IDで通知元と対象を結ぶ
- Backpressure: mailbox容量超過やmessage oversizedをaccept前に拒否する
- Explicit failure: silent dropを禁止し、senderへ失敗を返す
- Stuck-work check-in: 長時間background作業は一定時間後に状態確認する
- Safe steering: 実行中の危険な操作を途中で壊さず、安全な境界で指示を取り込む
- Audit: subscribe / wake / send / receive / reject / goal-state変更を記録する

## possible_component

`hooks/session-events/` + `agents/coordinator/`

概念interface:

`EventSubscription + AgentMailbox + DurableGoal + BackpressurePolicy`

## next_action

既存agents/hooksとの重複を確認し、最初は `PR changed / task completed / task failed` の3イベント、durable goal、capacity rejectionだけでPoCする。event source adapterはGitHub/Cursor等に依存しないinterfaceにする。
