# Inbox

AI業界ウォッチなどで見つけた、`agentic-workspace` の再利用可能な Skill / Agent Runtime / MCP / Policy 設計へ転用できそうな候補を一時保存する場所です。

## ルール

- ここは候補置き場であり、canonical な Skill ではありません。
- 1トピック1ファイルを基本とします。
- ベンダー固有機能そのものではなく、provider-neutral に抽象化できるパターンを優先します。
- 各メモには `source`、`observed_at`、`why_relevant`、`portable_pattern`、`next_action` を残します。
- 実装価値が確認できたものだけ `skills/`、`criteria/`、`hooks/`、`adapters/` 等へ昇格します。
- 顧客固有情報、秘密情報、運用ログ等は置きません。

## Status

- `candidate`: 未評価
- `evaluate`: PoC・設計比較対象
- `promote`: canonical 化候補
- `rejected`: 採用しない
