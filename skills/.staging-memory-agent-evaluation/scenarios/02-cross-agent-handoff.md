# Scenario 02: Cross-agent handoff

Claude Code側で `wish-002` の調査メモを追加し、Codex側で次の質問を行う。

> Command CenterのInbox / Workboardで、次に決めるべきことと未確定事項は何か？

## 合格条件

- 別エージェントから同じ記憶を取得できる
- 追加メモの作成者、日時、出典が追跡できる
- 未承認の推測が確定情報として混入しない

