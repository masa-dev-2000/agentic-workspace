---
status: candidate
observed_at: 2026-08-20
source: Anthropic Claude Code 2.1.236 cross-session SendMessage / goal behavior
---

# Event-driven agent coordination pattern

## why_relevant

複数Agentや複数sessionを協調させるとき、定期pollingやsilent dropに依存すると無駄な計算と見落としが増える。状態変化に応じたone-shot通知、明示的backpressure、stuck workのcheck-inを組み合わせる構造はprovider-neutralに再利用できる。

## portable_pattern

- Event-driven wakeup: idle / completed / failed等の状態変化を購読し、pollingを減らす
- One-shot notification: 一度だけ通知し、重複wake-upを避ける
- Correlation: session / task / request IDで通知元と対象を結ぶ
- Backpressure: mailbox容量超過やmessage oversizedをaccept前に拒否する
- Explicit failure: silent dropを禁止し、senderへ失敗を返す
- Stuck-work check-in: 長時間background作業は一定時間後に状態確認する
- Availability check: 送信前にrecipient sessionの有効性を確認する
- Audit: send / receive / reject / wake-upを記録する

## possible_component

`hooks/session-events/` + `agents/coordinator/`

概念interface:

`AgentMailbox + SessionEvent + BackpressurePolicy`

## next_action

既存agents/hooksとの重複を確認し、provider-neutralなmailbox/event interfaceを定義する。最初はidle/completed/failedの3イベントとcapacity rejectionだけでPoCする。
