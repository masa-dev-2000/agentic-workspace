---
status: candidate
observed_at: 2026-08-20
source:
  - OpenAI frontier-agent security controls around Astra
  - Anthropic Claude Code 2.1.238 plugin/MCP headersHelper and trust changes
---

# Agent security runtime pattern

## why_relevant

高能力Agentではsystem promptやTool permissionだけでは足りず、runtime側でネットワーク・資格情報・監視・停止を強制する必要がある。Claude Code 2.1.238では、Plugin/MCP取得時の短命header発行、project trust必須化、credential環境変数をhelperへ継承しない設計が入り、credential brokerをAgent外に置く具体例が増えた。

## portable_pattern

- Network egress: deny-by-default / allowlist
- Sandbox: tool/code executionを隔離
- Credential broker: Agentへ長期secretを渡さず、必要時に短命token/headerを発行
- Credential isolation: helper/subprocessへ親processのcredential envを無条件継承しない
- Trust boundary: project/plugin/MCP由来のcredential helperは明示的trust後だけ実行
- Install/update consent: credential helper等の外部commandは実行前に内容を見せて承認
- Monitoring: tool use / boundary crossingを別classifier・investigatorで監視
- Stop rule: 高リスク判定や判定不能時にfail-closed
- Audit: 全tool call・credential issuance・policy decisionを記録
- Human gate: 高影響操作は承認必須

## possible_component

`criteria/agent-runtime-policy/` + `hooks/` + `adapters/credential-broker/` + validator

重要点:
- 安全ルールを文章だけで宣言しない
- executable enforcement pathを持たせる
- model/provider固有のclassifierに依存しない
- raw secretではなく短命capabilityをAgentへ渡す

## next_action

既存criteria/hooksの安全制御と重複確認し、network / credential / approval / auditを共通policy interfaceとして定義する。credential部分は `request_capability(scope, ttl) -> ephemeral credential` の最小interfaceでPoCする。
