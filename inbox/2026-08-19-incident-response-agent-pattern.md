---
status: candidate
observed_at: 2026-08-19
source: Anthropic production CI/CD incident-response architecture
---

# Incident response agent pattern

## why_relevant

Coding Agentだけでなく、CI障害・本番インシデントを別Agentで受け持つ構造は、agentic-workspaceの運用系Skillへ転用価値が高い。

## portable_pattern

- Trigger: deterministic alert / incident event
- Orchestrator: incident coordinator agent
- Executors: logs / metrics / code / deployment / ticket investigation agents
- Memory: 過去incidentのlessonを蓄積
- Skills: 頻出パターンを再利用可能Skillへ昇格
- Tools: GitHub, observability, Kubernetes, incident management via MCP/API
- Guardrails: merge・重大操作は人間承認、alert条件はdeterministic rule

## possible_skill

`skills/incident-triage/`

想定責務:
1. incidentの事実収集
2. 仮説列挙
3. 根拠付きroot-cause候補
4. 追加確認ツールの選択
5. 修正案と影響範囲
6. 人間へescalation

## next_action

既存のreview / CI系Skillとの重複を確認し、汎用incident-triage Skillとして切り出せるか評価する。
