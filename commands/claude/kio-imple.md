GitHubイシューを実装してPRを作成する。

引数: issue番号（カンマ区切りで複数指定可）
例: /kio-imple 120, 121, 117

## プロジェクト設定の読み込み（最初に実行）

プロジェクトの `CLAUDE.md` の「KIO 設定」セクションから以下を読む。記載がなければデフォルトを使う:
- `base_branch`（PR の base / 派生元）: デフォルト `main`
- `impl_branch_prefix`: デフォルト `feat/impl-`
- `typecheck_dirs`（任意・配列）: 記載があればそのディレクトリで `npx tsc --noEmit` を実行

## Phase 1: Spec Gathering（仕様収集）

issue番号を一括取得:
```bash
gh issue view <N> --json number,title,body -q '"\(.number): \(.title)\n\(.body)"'
```
キーワードで影響ファイルをgrepしてから Read する（全ディレクトリ走査しない）。

## Phase 2: Implementation（実装）

ブランチ作成（issue番号が1つなら `<impl_branch_prefix><N>`、複数なら `<impl_branch_prefix><N1>-<N2>`）:
**必ず `base_branch` から分岐させること:**
```bash
git worktree add .claude/worktrees/impl-<numbers> -b <impl_branch_prefix><numbers> <base_branch>
```

実装後、`typecheck_dirs` が設定されていれば各ディレクトリで TypeScript チェック:
```bash
cd <dir> && npx tsc --noEmit 2>&1 | head -30
```
エラーがあれば必ず修正してから次へ。

## Phase 3: PR作成

変更ファイルを明示的にステージ（`git add .` は使わない）してコミット後:
```bash
git push origin <impl_branch_prefix><numbers>
gh pr create \
  --base <base_branch> \
  --title "feat/fix: <説明> (#N #M)" \
  --body "## 概要\n...\n## 変更ファイル\n...\n## テスト手順\n...\nclose #N\nclose #M"
```

## Phase 4: Self-Review

```bash
gh pr review <PR番号> --comment --body "## セルフレビュー\n### ✅ 実装内容\n### ⚠️ リスク・注意点\n### 🧪 テスト確認項目\n### 📝 残課題"
```

## 完了報告フォーマット

```
✅ PR #<N> 作成完了
タイトル: <PRタイトル>
ブランチ: <impl_branch_prefix><numbers>
対象: closes #X[, closes #Y]
URL: https://github.com/...
```

## トークン節約ルール
- grep → Read の順序を守る
- `--json` + `-q` でgh出力を絞る
- `head -30` でTSエラーを打ち切る
- 同一ファイルの再Read禁止
- 複数issue操作は1bashコマンドにまとめる
