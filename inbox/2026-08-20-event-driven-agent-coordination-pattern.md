---
status: candidate
observed_at: 2026-08-20
source:
  - Anthropic Claude Code 2.1.236 cross-session SendMessage / goal behavior
  - Anthropic Claude Code 2.1.239 durable goal / peer discovery / adaptive check-in behavior
  - Cursor Cloud Agents and Cursor Harness Improvements (2026-08-19)
---

# Event-driven agent coordination pattern

## why_relevant

複数Agentや長時間Agentを協調させるとき、定期pollingやsilent dropに依存すると無駄な計算と見落としが増える。Claude Codeのone-shot通知/backpressureと、CursorのPR・Slack等のevent subscriptionを組み合わせると、provider-neutralな常駐Agent制御へ抽象化できる。

## portable_pattern

- Event source subscription: PR / thread / schedule / task状態などを購読する
- Event-driven wakeup: idle / changed / completed / failed等でAgentを再開し、pollingを減らす
- Durable goal: 一度のturnではなく、完了条件を満たすまでgoalを保持し、session resume後も復元する
- One-shot notification: 必要な通知は一度だけ送り、重複wake-upを避ける
- Peer discovery: 到達可能なAgent/sessionと自身のidentityを明示的に列挙できるようにする
- Correlation: session / task / request / event-source IDで通知元と対象を結ぶ
- Backpressure: mailbox容量超過やmessage oversizedをaccept前に拒否する
- Explicit failure: silent dropを禁止し、senderへ失敗を返す
- Adaptive check-in: 長時間background作業は固定pollではなく、30m -> 1h -> 2h等のbackoffで状態確認する
- Lease/keepalive: 長いsetupやhook中もrunner/session leaseを維持し、idle reapを防ぐ
- Safe steering: 実行中の危険な操作を途中で壊さず、安全な境界で指示を取り込む
- Trace continuity: hookやdeferred tool実行をまたいでも同一turn/taskのtrace correlationを維持する
- Audit: subscribe / wake / send / receive / reject / goal-state変更を記録する

## possible_component

`hooks/session-events/` + `agents/coordinator/`

概念interface:

`EventSubscription + AgentMailbox + DurableGoal + PeerRegistry + BackpressurePolicy + LeaseManager`

## next_action

既存agents/hooksとの重複を確認し、最初は `PR changed / task completed / task failed` の3イベント、durable goal、capacity rejection、resume復元、adaptive backoffだけでPoCする。event source adapterはGitHub/Cursor等に依存しないinterfaceにする。
