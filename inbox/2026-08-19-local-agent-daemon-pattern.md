---
status: candidate
observed_at: 2026-08-19
source: Anthropic Claude Science local/HPC execution architecture
---

# Local agent daemon pattern

## why_relevant

データをクラウドへ移さず、Agentだけをローカル/HPC/自社Cloudへdispatchする構造は、機密データ・研究・顧客環境で使えるprovider-neutralな実行パターン。

## portable_pattern

- UI / plannerはLLM側
- Local daemonがデータ・ツール・ジョブを管理
- 実データはユーザー環境に残す
- Heavy jobsはlocal GPU / HPC / SLURM / own cloudへdispatch
- Credentialとfilesystem accessはdaemon側で制御
- LLMには必要最小限の結果だけ返す

## possible_component

`adapters/local-runner/` または provider-neutral runner interface

想定interface:
- discover_capabilities
- submit_job
- poll_job
- cancel_job
- fetch_artifact
- enforce_resource_limits

## next_action

既存adaptersとconfig/wiring.jsonを確認し、runner abstractionを追加する価値があるか評価する。
