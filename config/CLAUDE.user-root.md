# グローバル設定

## 開発フロー: dev-flow プラグイン

issue登録→issue深掘り→実装→レビュー→マージ(→本番反映)という開発フローは、プロジェクトごとに`.claude/`へ複製するのではなく、`dev-flow`プラグインとして共通化している。詳細ロジック(role検出・dispatch・PR作成・マージ戦略等)はすべてプラグイン側(`commands/`, `agents/coding-loop-dev.md`, `hooks/`, `skills/`)に存在する。プロジェクト側が持つのは「プロジェクト固有の値」と「role↔APIパスの対応」だけ。

### 新規プロジェクトへの導入手順

1. `dev-flow`プラグインをこのマシンに導入済みか確認する(未導入ならmarketplace経由でインストール)。
2. プロジェクトルートの`CLAUDE.md`に「開発フロー設定」セクションを追加し、以下のYAMLフェンスブロックを埋める:
   ````
   <!-- workflow-config:start -->
   ```yaml
   review_account: <PR承認/差し戻し実行用アカウント>
   pr_account: <実装PR作成側のデフォルトアカウント>
   dev_url: <dev環境URL>
   e2e_accounts: []
   prod_branch: main
   prod_deploy_commands: ""
   prod_url: ""
   supabase_project_id: <あれば>
   escalation_changes_requested_threshold: 2
   ```
   <!-- workflow-config:end -->
   ````
3. `src/app/<role>/`構成が前提(role名=ディレクトリ名、`page.tsx`が存在するディレクトリのみ自動検出される)。role→APIパスの対応が自明でない場合は`.claude/role-api-map.json`を作成し人手で確定する(共通基盤は`shared`キーとして予約済み)。要件が固まっていない新規プロジェクトでは、`/requirements-design`セッションでヒアリング→モック→要件定義書(`docs/requirements.md`)を作ってから着手する。
4. `.claude/handoff/`と`.claude/.{deepen,impl,review,merge}-active.*`・`.claude/.{deepen,impl,review,merge}-dispatcher-state.*.json`マーカーファイル(セッションIDごとに分離される)を`.gitignore`に追加する。
5. `/deepen-on`・`/impl-on`・`/review-on`・`/merge-on`(マージセッションのdevelop取り込みまでは自動、本番反映`/dev-mainmerge`だけは常に人間の明示起動)で1サイクル動作確認する。

詳細ロジックはすべてプラグイン側に存在するため、プロジェクト側の`CLAUDE.md`にコマンドロジックを複製しない。

## その他

- Follow the user's instructions precisely, and within that scope act autonomously: gather the necessary context and complete the requested work end-to-end in this run, asking questions only when essential information is missing or the instructions are critically ambiguous.
