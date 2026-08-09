開発環境（dev）へのマージ＆デプロイを行う。

引数なしの場合はレビュー済みPRを一覧表示して選択させる。PR番号指定の場合は直接対象にする。

## プロジェクト設定の読み込み（最初に実行）

プロジェクトの `CLAUDE.md` の「KIO 設定」セクションから以下を読む:
- `base_branch`（dev環境にマージするブランチ）: デフォルト `main`
- `dev_deploy_commands`（必須・複数行）: PRマージ後に実行するデプロイコマンド列
- `dev_url`（任意）: 完了報告に表示するdev環境URL

`dev_deploy_commands` が未定義の場合は **「プロジェクトのCLAUDE.mdに `dev_deploy_commands` が未設定です。デプロイ手順を教えてください」とユーザーに確認**してから進む（勝手にwrangler等を実行しない）。

## ⛔ 絶対禁止事項（省略・例外なし）

このコマンドは**開発環境専用**です:
- 本番環境向けのデプロイコマンド・フラグ（例: `--env production`）は絶対に実行しない
- 本番DB・本番Pages/Workersプロジェクトへの操作は絶対に行わない
- 設定にない手順を独自判断で追加しない

## Phase 1: 対象PRの確認

引数なしの場合は**Approve済み（CHANGES_REQUESTEDが残っていない）のopenなPR**を絞り込んで表示する。`reviewDecision` フィールドはブランチ保護ルールが無いリポジトリでは常に空になるため使わず、`reviews` 配列の各 `state` を直接見る:

```bash
gh pr list --state open --json number,title,headRefName,reviews \
  --jq '.[] | select(([.reviews[].state] | any(. == "APPROVED")) and ([.reviews[].state] | any(. == "CHANGES_REQUESTED") | not)) | "#\(.number) [\(.headRefName)] \(.title)"'
```

Approve済みPRが0件の場合は「Approve済みのPRはありません。全オープンPRを表示しますか？」と確認し、yならば全件表示する:

```bash
gh pr list --state open --json number,title,headRefName,reviews \
  --jq '.[] | "#\(.number) [\(.headRefName)] \(.title) (レビュー\(.reviews | length)件)"'
```

一覧を表示して「どのPRをマージしますか？」と確認する。

## Phase 2: 再レビュー（見落とし防止チェックリスト）

```bash
gh pr diff <number>
```

以下の全項目を確認してからユーザーに報告する:

#### 🎯 要件適合
- issueの仕様をすべて実装しているか / close #N の参照があるか

#### 🔒 セキュリティ
- XSS（ユーザー入力を直接DOM挿入）がないか
- SQLインジェクション（文字列結合クエリ）がないか
- 秘密鍵・envのハードコードがないか
- 新規APIに認証・認可チェックがあるか

#### ⚡ パフォーマンス
- useEffectの無限ループの可能性がないか
- N+1クエリ（ループ内DBアクセス）がないか
- 大きなライブラリの新規importがないか

#### 🧹 コード品質
- console.log / TODO / FIXME が残っていないか
- TypeScript の any 型の不必要な使用がないか
- 未使用import・変数がないか

#### 💥 破壊的変更・副作用
- 共有コンポーネント変更の影響範囲を確認したか
- DBマイグレーションファイルがあるか（必要な場合）
- APIレスポンス形式変更でフロントも対応しているか

#### 🔀 他PRとの競合
```bash
gh pr list --state open --json number,title,files \
  --jq '.[] | select(.number != <対象PR番号>) | "#\(.number): \(.files | map(.path) | join(", "))"'
```

## Phase 3: ユーザー承認

レビュー結果を報告し、「マージしてよいですか？ (y/n)」と確認する。
問題あり（❌）の場合はマージせず修正内容を伝えて終了。

## Phase 4: マージ＆Devデプロイ（承認後）

```bash
gh pr merge <number> --merge --delete-branch
git checkout <base_branch> && git pull origin <base_branch>
```

続いて、プロジェクト設定の `dev_deploy_commands` に記載された手順を**そのまま順番に実行する**。
（設定にないコマンドは追加しないこと）

## 完了報告 & E2Eテスト自動起動

デプロイ完了後に以下を表示してからE2Eテストを自動で開始する:

```
✅ Dev環境マージ完了
PR #<N>: <タイトル>
Webデプロイ: <dev_url が設定されていれば記載>
Closes: #<issue番号>

🧪 E2Eテストを開始します...
```

デプロイ完了を確認後、**続けて /kio-e2e <PR番号> を実行する**（ユーザーへの確認不要、自動で開始）。
