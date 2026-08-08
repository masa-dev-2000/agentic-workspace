# Memory Agent Evaluation

Claude Code、Codex、mindwalk、Basic Memory、agent-memory、QMを、同じ実務シナリオで比較するための検証プロジェクト。

## 目的

`personal` 配下の実験プロジェクトで発生する「やりたいこと」を、記憶・検索・判断・実装観測・組織運用の観点から評価する。

## 評価対象

- agent-memory: ローカルMarkdown記憶とClaude/Codex間の再利用
- Basic Memory: MCP、意味検索、知識グラフ、ノート更新
- mindwalk: エージェントの探索・編集・検証過程の可視化
- QM: 組織知識、権限、エージェント、サンドボックスのセルフホスト運用

## 原則

- 本番データ、顧客情報、認証情報は接続しない
- 各ツールの保存先・外部通信・権限・削除方法を記録する
- 同じ入力と同じ合格条件で比較する
- 「導入できた」と「役に立った」を分けて記録する

## 使い方

1. `fixtures/wish-list.jsonl` を各ツールへ登録する
2. `scenarios/01-capture-and-recall.md` を実行する
3. `scenarios/02-cross-agent-handoff.md` を実行する
4. `scenarios/03-implementation-observation.md` を実行する
5. `scenarios/04-organization-platform.md` はQMの展開準備ができてから実行する
6. `results/` に証拠と評価を保存する

