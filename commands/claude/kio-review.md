PR作成済みのイシューについて、実装内容・方針・変更画面・リスクをコードを読まなくても分かる言葉で説明し、ユーザーのOK/NGを受けてGitHubの正式なPRレビュー（Approve / Request changes）として記録する。

引数なしの場合はPR作成済みの全PRを番号順に1本ずつ見ていく。PR番号指定の場合はそのPRのみ対象にする。

## Phase 1: 対象PRの収集

```bash
gh pr list --state open --json number,title,headRefName,reviews \
  --jq 'sort_by(.number) | .[] | {number, title, branch: .headRefName, decided: ([.reviews[].state] | any(. == "APPROVED" or . == "CHANGES_REQUESTED"))}'
```

`decided` が `false`（誰もApprove/Request changesしていない＝コメントのみ、またはレビュー自体が無い）のものをレビュー未実施として対象にする。
（`reviewDecision` フィールドはブランチ保護ルールが無いリポジトリでは常に空になるため使わない。引数でPR番号が指定された場合はそのPRのみ）

対象PRが0件の場合は「レビュー待ちのPRはありません。」と表示して終了。

複数ある場合は件数を表示してから1本目を開始する:
```
📋 レビュー待ち N件: #140, #141, #142 ...
--- 1本目 / N本 ---
```

## Phase 2: PR内容の分析（1本ごとに実行）

### 2-1. PR基本情報とdiff取得
```bash
gh pr view <number> --json title,body,files,additions,deletions
gh pr diff <number>
```

### 2-2. 関連issueの仕様確認
PRタイトル・本文から関連issue番号を抽出し、仕様を確認する:
```bash
gh issue view <N> --json title,body -q '"#\(.number): \(.title)\n\(.body)"'
```

### 2-3. ユーザーへの説明（コードを読まなくても分かる言葉で）

以下の4項目を**技術用語を避けた平易な言葉**でまとめて表示する:

---
**PR #<番号>: <タイトル>**
対象issue: #N, #M

#### ✅ 実装した機能の概要
何ができるようになったか / 何が直ったか を1〜3文でユーザー目線で説明する。
（例: 「画像の詳細画面でEscapeキーを押すと、元の一覧画面に直接戻れるようになりました」）

#### 🔧 実装方針
どういう考え方・設計で作ったかを非エンジニア向けに説明する。
（例: 「ブラウザの履歴を遡るのではなく、URLを直接指定して遷移する方式に変更しました」）

#### 📄 変更された画面・機能
変更があったページや機能を箇条書きで列挙する。
（例: 「・画像一覧ページ　・画像詳細ページ　・カメラ管理ページ」）

#### ⚠️ リスク・注意点
副作用や注意点があれば記載する。なければ「特になし」。
（例: 「既存のブックマーク URLの動作に影響なし」）
---

## Phase 3: ユーザーのOK/NG確認

説明後に以下を表示して入力を待つ:
```
このPRの実装方針はいかがですか？
  [ok] 問題なし → GitHubにOK記録
  [ng] 修正が必要 → 修正内容を教えてください
  [skip] このPRは後回し
```

### OKの場合
GitHubのPRに正式なApproveレビューを記録する（`/kio-devmerge` はこの Approve 状態をトリガーに対象PRを判定する）:
```bash
gh pr review <number> --approve --body "## ✅ オーナーレビュー OK

実装方針・機能概要を確認しました。問題ありません。
/kio-reviewコマンドによる確認 $(date '+%Y-%m-%d')"
```

完了後:「✅ #<番号> Approve記録しました。/kio-devmerge で dev環境にマージできます。」と表示し次のPRへ。

### NGの場合
「どのような修正が必要か教えてください」と聞き、入力された内容をGitHubの正式なRequest changesとして記録する:
```bash
gh pr review <number> --request-changes --body "## ❌ オーナーレビュー NG

**修正指示:**
<ユーザーが入力した修正内容>

/kio-reviewコマンドによる確認 $(date '+%Y-%m-%d')"
```

完了後:「❌ #<番号> Request changes記録しました。修正後に再度 /kio-review <番号> を実行してください。」と表示し次のPRへ。

> 注: `--approve` / `--request-changes` は、PR作成者自身のアカウントでは実行できない（GitHubの仕様）。プロジェクトの `CLAUDE.md` の「KIO 設定」に `review_account`（レビュー用）/ `pr_account`（PR作成側）が設定されていれば、Approve/Request changes 実行の直前に
> ```bash
> gh auth switch --user <review_account>
> ```
> で切り替え、実行後すぐに
> ```bash
> gh auth switch --user <pr_account>
> ```
> で元に戻す（`gh auth switch` はマシン全体で共有されるactiveアカウント設定なので、切り替えている間は他セッションの `gh pr create` 等が同じアカウント名になる点に注意し、できるだけ短時間で済ませる）。
> `review_account` が未設定、または `gh auth status` のユーザーとPR作成者が同一のままの場合は、`--comment` にフォールバックし、本文に判定結果を明記した上でユーザーにその旨を伝える。

### skipの場合
記録せず次のPRへ進む。

## Phase 4: 完了報告

全PR確認後にサマリーを表示:
```
📊 レビュー完了サマリー
✅ OK: #140, #143, #144（3件）
❌ NG: #142（1件）
⏭️ スキップ: #141（1件）

OKのPRは /kio-devmerge で dev環境にマージできます。
```
