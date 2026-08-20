---
status: candidate
observed_at: 2026-08-20
source: Cursor Cloud Agents and Cursor Harness Improvements (2026-08-19)
---

# Isolated subagent execution pattern

## why_relevant

並列Subagentが同じworkspaceやprocessを共有すると、ファイル競合、状態汚染、credential漏れ、テスト結果の相互干渉が起きやすい。各Subagentへ独立した実行環境・project copy・clean contextを割り当てる構造は、provider-neutralなMulti-Agent Runnerへ転用価値が高い。

## portable_pattern

- 1 subagent = 1 isolated workspace / sandbox
- task開始時にclean project snapshotを作る
- credential / network / resource policyもsubagent単位で分離する
- parentとの共有は明示的artifact / patch / result messageだけに限定する
- 同じ対象を変更するtaskはbranch/worktree等で衝突を隔離する
- fresh environmentで親Agentの変更を独立検証できる
- parallel swarmはCPU / memory / token / concurrency budgetで上限管理する
- 終了時にenvironmentを破棄または再利用可能snapshotとして管理する

## possible_component

`agents/runner/` + `adapters/sandbox/`

`SubagentTask -> IsolatedEnvironment -> ResultArtifact -> ParentAgent`

## next_action

既存runner/adaptersを確認し、local worktree / container / cloud VMを同じinterfaceで扱える `ExecutionEnvironment` を設計する。PoCではGit worktree + process isolationから始め、cloud VMはadapterに留める。
