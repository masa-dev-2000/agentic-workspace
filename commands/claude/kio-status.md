GitHubイシューの開発進捗状況をパイプライン形式で一覧表示する。

引数:
- なし / `-o`: open issues のみ（デフォルト）
- `-c`: closed issues のみ
- `-a`: open + closed 全て

## ステータス定義

| ステータス | 絵文字 | 条件 |
|-----------|--------|------|
| 保留 | ⏸️ | `hold_label`（設定値、デフォルト「保留」）が付いている |
| 未着手 | 🔴 | Issueオープン・関連PR/ブランチなし |
| 着手済み | 🟡 | `<impl_branch_prefix><N>` ブランチが存在するがPRなし |
| PR作成済み | 🔵 | オープンなPRが関連している |
| レビュー済み | 🟣 | 関連PRにレビューコメントが1件以上ある |
| dev環境済み | 🟠 | 関連PRが `base_branch` にマージ済み（Issueはまだオープン） |
| 本番環境済み | ✅ | Issueがクローズ済み（/kio-mainmerge完了） |

## プロジェクト設定の読み込み（最初に実行）

プロジェクトの `CLAUDE.md` の「KIO 設定」セクションから読む。記載がなければデフォルト:
- `impl_branch_prefix`: デフォルト `feat/impl-`
- `base_branch`: デフォルト `main`
- `hold_label`: デフォルト `保留`

## 実行手順

### Step 1: Issueデータ取得

```bash
# open の場合
gh issue list --state open --json number,title,labels,createdAt --limit 100 \
  --jq '.[] | {number, title, labels: [.labels[].name], state: "OPEN"}'

# closed の場合（-c）
gh issue list --state closed --json number,title,labels,closedAt --limit 100 \
  --jq '.[] | {number, title, labels: [.labels[].name], state: "CLOSED", closedAt: .closedAt[:10]}'

# 全件（-a）は --state all で同様に
```

### Step 2: PR一覧取得（open + merged 両方）

```bash
gh pr list --state all \
  --json number,title,state,mergedAt,headRefName,reviews,closingIssuesReferences \
  --limit 200 \
  --jq '.[] | {
    number,
    title,
    state,
    merged: (.mergedAt != null),
    branch: .headRefName,
    reviewCount: (.reviews | length),
    linkedIssues: [.closingIssuesReferences[].number]
  }'
```

### Step 3: リモートブランチ取得（着手済み検出用）

```bash
git fetch --prune 2>/dev/null
git branch -r | grep -oE '<impl_branch_prefix>[0-9]+(-[0-9]+)*' | sort -u
```

### Step 4: Issueとデータを相関分析してステータス判定

以下のロジックでIssueごとのステータスを決定する:

**PRとIssueの紐付けルール（優先順位順）:**
1. `closingIssuesReferences` にIssue番号が含まれる
2. PRタイトルに `(#<N>` または `#<N>)` または `#<N> #` の形式でIssue番号が含まれる
3. ブランチ名が `<impl_branch_prefix><N>` または `<impl_branch_prefix><N>-<M>` の形式でIssue番号が含まれる

**ステータス判定ロジック（上から優先）:**
1. Issue のラベルに `hold_label`（デフォルト「保留」）が含まれる → ⏸️ 保留
2. Issue が CLOSED → ✅ 本番環境済み
3. Issue が OPEN + 紐付きPRが MERGED → 🟠 dev環境済み
4. Issue が OPEN + 紐付きPRが OPEN + reviewCount >= 1 → 🟣 レビュー済み
5. Issue が OPEN + 紐付きPRが OPEN + reviewCount == 0 → 🔵 PR作成済み
6. Issue が OPEN + `<impl_branch_prefix><N>` ブランチが存在 → 🟡 着手済み
7. それ以外 → 🔴 未着手

### Step 5: 結果をMarkdownテーブルで表示

```
## 📊 Issue進捗状況（open N件）

| # | ステータス | ラベル | タイトル | PR |
|---|-----------|--------|---------|-----|
| #145 | 🔵 PR作成済み | ✨ enhancement | カメラ最終受信表示 | #145 |
| #136 | 🔴 未着手 | ✨ enhancement | カメラ別画像フィルタリング | - |
| #135 | 🔴 未着手 | 🔍 investigation | SMTP移行調査 | - |
```

**ラベル絵文字マッピング:**
- bug → 🐛 bug
- enhancement → ✨ enhancement
- infrastructure → 🏗️ infrastructure
- investigation → 🔍 investigation
- ラベルなし → —

**PR列の表示:**
- オープンPR → `#<番号>` (open)
- マージ済みPR → `#<番号>` ✓
- PRなし → `-`

表の下にステータス別集計を表示:
```
🔴 未着手: N件  🟡 着手済み: N件  🔵 PR作成済み: N件
🟣 レビュー済み: N件  🟠 dev環境済み: N件  ✅ 本番環境済み: N件
```
