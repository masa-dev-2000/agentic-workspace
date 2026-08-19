---
status: candidate
observed_at: 2026-08-20
source: GitHub Agentic Workflows v0.87.0 / Safe Outputs specification
---

# Safe output gateway pattern

## why_relevant

Agentに外部システムへの直接変更権限を持たせず、Agentは型付きの変更提案だけを出し、別の実行層が検証後に反映する構造。provider-neutralなAgent Runtimeのwrite boundaryとして転用価値が高い。

## portable_pattern

- Agentは read / reason / propose に限定
- proposalはoutput typeごとのschemaで固定
- trusted handlerがschema・対象・許可範囲を検証してから実行
- preview / dry-runでは外部変更を行わない
- agentが指定した対象IDはtrusted workflow stateとの一致を確認
- schema外のfieldは実行前に削除
- 検証失敗時は変更しない
- proposal / validation / executionを分離して記録

## possible_component

`criteria/safe-output-policy/` + `adapters/<provider>/write-gateway` + validator

`AgentProposal -> PolicyValidator -> PrivilegedHandler -> ExternalSystem`

## next_action

既存のagent-security-runtime候補と責務分離を確認し、GitHub write、メール送信、issue作成、deploy等へ共通化できるtyped safe-output interfaceをPoCする。
