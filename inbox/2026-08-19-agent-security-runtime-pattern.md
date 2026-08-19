---
status: candidate
observed_at: 2026-08-19
source: OpenAI frontier-agent security controls around Astra
---

# Agent security runtime pattern

## why_relevant

高能力Agentではsystem promptやTool permissionだけでは足りず、runtime側でネットワーク・資格情報・監視・停止を強制する必要がある。provider-neutralなpolicy層として転用価値が高い。

## portable_pattern

- Network egress: deny-by-default / allowlist
- Sandbox: tool/code executionを隔離
- Credential: standing privilegeを減らし短命token化
- Monitoring: tool use / boundary crossingを別classifier・investigatorで監視
- Stop rule: 高リスク判定や判定不能時にfail-closed
- Audit: 全tool callとpolicy decisionを記録
- Human gate: 高影響操作は承認必須

## possible_component

`criteria/agent-runtime-policy/` + `hooks/` + validator

重要点:
- 安全ルールを文章だけで宣言しない
- executable enforcement pathを持たせる
- model/provider固有のclassifierに依存しない

## next_action

既存criteria/hooksの安全制御と重複確認し、network / credential / approval / auditを共通policy interfaceとして定義できるか評価する。
