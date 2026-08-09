本番環境（main）へのマージ＆デプロイを行う。

⚠️ このコマンドは本番環境に影響します。各フェーズを省略せず必ず実行してください。

引数なしの場合はopen PRを一覧表示して選択させる。PR番号指定の場合は直接対象にする。

## プロジェクト設定の読み込み（最初に実行）

プロジェクトの `CLAUDE.md` の「KIO 設定」セクションから以下を読む:
- `prod_branch`（本番ブランチ）: デフォルト `main`
- `prod_deploy_commands`（必須・複数行）: 本番デプロイのコマンド列。**CI経由を強く推奨**（例: `gh workflow run ...`）。ローカルからの直接デプロイは原則禁止
- `db_migration_command`（任意）: DBマイグレーション実行コマンドのテンプレート
- `prod_url`（任意）: 完了報告に表示する本番URL

`prod_deploy_commands` が未定義の場合は **「プロジェクトのCLAUDE.mdに `prod_deploy_commands` が未設定です。本番デプロイ手順を教えてください」とユーザーに確認**してから進む（勝手にwrangler等を実行しない）。

## Phase 1: 対象PRの確認

```bash
gh pr list --state open --json number,title,headRefName \
  --jq '.[] | "#\(.number) [\(.headRefName)] \(.title)"'
```

## Phase 2: 本番ブランチの現状調査（コンフリクト防止）

### 2-1. `prod_branch` の直近コミット確認
```bash
git fetch origin <prod_branch>
git log origin/<prod_branch> --oneline -15
```

### 2-2. PRブランチと `prod_branch` の divergence 確認
```bash
git log origin/<prod_branch>..<PR-branch> --oneline
git log <PR-branch>..origin/<prod_branch> --oneline
```

### 2-3. コンフリクトのドライラン（必須）
```bash
gh pr checkout <number>
git fetch origin <prod_branch>
git merge origin/<prod_branch> --no-commit --no-ff 2>&1
git status --short
git merge --abort 2>/dev/null || true
git checkout <prod_branch>
```
コンフリクト検出時は即停止して解消手順を案内する。

### 2-4. 他のopen PRとのファイル競合チェック
```bash
gh pr list --state open --json number,title,files \
  --jq '.[] | select(.number != <対象PR番号>) | "#\(.number): \(.files | map(.path) | join(", "))"'
```

### 2-5. dev環境テスト確認
```bash
gh pr view <number> --json comments,reviews \
  --jq '[.comments[].body, .reviews[].body] | join("\n")'
```
dev確認の記録がない場合は警告する。

## Phase 3: 本番前レビュー

```bash
gh pr diff <number>
```

以下を全項目チェック:

#### 🎯 要件・品質
- issueの仕様を満たしているか / TypeScriptエラーなしか

#### 🔒 本番固有のセキュリティ
- 環境変数・秘密鍵のハードコードがないか
- `ENVIRONMENT === 'development'` 限定処理が本番で動かないか
- 新規APIに認証・認可があるか / Rate Limit・CORS設定が正しいか

#### 🗄️ DB・インフラ
- DBマイグレーションがある場合、本番適用コマンド（`db_migration_command`）を確認したか
- 設定ファイルの本番セクションに影響がないか

#### 🔄 後方互換性
- APIレスポンス形式変更で既存クライアントが壊れないか
- Cookie・ローカルストレージのキー名変更で既存セッションが壊れないか

## Phase 4: 二重承認（必須）

レビュー結果報告後、以下を表示:
```
本番環境（<prod_url または プロジェクト名>）にデプロイします。
よろしいですか？ (yes と入力して確認)
```
「yes」以外ではマージしない。

## Phase 5: マージ＆本番デプロイ（承認後）

### 5-1. PRマージ

```bash
gh pr merge <number> --merge --delete-branch
```

### 5-2. DBマイグレーション（該当する場合のみ）

PRにDBマイグレーションが含まれる場合、**デプロイの前に**実行する。
`db_migration_command` が設定されていればそれを使用し、未設定なら手順をユーザーに確認する。

```bash
git checkout <prod_branch> && git pull origin <prod_branch>
# db_migration_command を実行
```

### 5-3. 本番デプロイ

プロジェクト設定の `prod_deploy_commands` に記載された手順を**そのまま順番に実行する**。
（設定にないコマンドは追加しないこと。ローカルからの直接デプロイ系コマンドは設定で明示されていない限り実行しない）

## Phase 6: Issueクローズ（必須）

本番デプロイが完了したら、関連Issueをクローズする。
**Issueのクローズはこのタイミングでのみ行う。devmerge時点では絶対にクローズしない。**

```bash
# PRに紐づくIssue番号を取得
gh pr view <number> --json closingIssuesReferences \
  --jq '.closingIssuesReferences[].number'

# 各Issueをクローズ
gh issue close <issue-number> --comment "本番デプロイ完了（PR #<number>）"
```

## Phase 7: 完了報告

```
🚀 本番デプロイ完了
PR #<N>: <タイトル>
本番URL: <prod_url が設定されていれば記載>
DBマイグレーション: <実行した場合のみ>
Closed: #<issue番号>

動作確認をお願いします。
```
