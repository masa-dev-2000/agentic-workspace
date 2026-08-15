GitHubイシューを実装してPRを作成する。

引数: issue番号（カンマ区切りで複数指定可）
例: /kio-imple 120, 121, 117

レビュー工程の正本は `docs/CODE_REVIEW.md`。実装担当の自己確認だけでPRを作らず、
PR作成前に必ず別コンテキストの読み取り専用レビューを通す。

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
受け入れ条件が検証可能でなければ、推測で実装せずユーザーへ確認する。

## Phase 2: Implementation（実装と実行確認）

ブランチ作成（issue番号が1つなら `<impl_branch_prefix><N>`、複数なら `<impl_branch_prefix><N1>-<N2>`）:
**必ず `base_branch` から分岐させること:**
```bash
git worktree add .claude/worktrees/impl-<numbers> -b <impl_branch_prefix><numbers> <base_branch>
```

バグ修正は可能な限り、修正前に失敗するテストまたは再現コマンドを実行してから直す。
実装後はissueの受け入れ条件に対応するテスト・Build・実行確認を行い、実際のCommandと結果を保存する。

`typecheck_dirs` が設定されていれば各ディレクトリで TypeScript チェック:
```bash
cd <dir> && npx tsc --noEmit 2>&1 | head -30
```
エラーがあれば必ず修正してから次へ。「動くはず」は検証結果として扱わない。

## Phase 3: Independent Review（別コンテキストの技術レビュー）

`adversarial-reviewer` Agentを変更Diffに対して起動する。実装担当自身による要約や自己評価を、
レビュー結果の代わりにしてはならない。

1. Reviewerは読み取り専用で、変更Diffと関連Call site・Testを直接確認する。
2. `[INTRODUCED]` のmedium以上を実装担当がSourceで再確認し、正しい指摘なら修正と回帰Testを追加する。
3. 修正後に同じReviewerを再実行する。
4. `docs/CODE_REVIEW.md` のReview-round budgetで止め、残る指摘・反論・Riskを隠さず記録する。

合格条件:
- medium以上のintroduced findingが0件、または
- canonical review-round budget終了後に残件と理由をPRへ明記できる状態。

`adversarial-reviewer` が利用できない場合だけ、`$review-agent` または同じChecklistによる別Sessionの
読み取り専用ReviewへFallbackし、その例外をPRへ記録する。

## Phase 4: PR作成

変更ファイルを明示的にステージ（`git add .` は使わない）してコミット後:
```bash
git push origin <impl_branch_prefix><numbers>
gh pr create \
  --base <base_branch> \
  --title "feat/fix: <説明> (#N #M)" \
  --body-file <PR本文ファイル>
```

PR本文は `.github/PULL_REQUEST_TEMPLATE.md` の各欄を埋める。最低限、次を含める:

- 関連issueと受け入れ条件
- 変更概要
- 実行した検証Commandと実結果
- Independent Reviewer名、対象、Round数、最終結果
- 残るintroduced medium以上の指摘（なければ `none`）
- CodeRabbit状態（`pending` / `clean` / `issues remain` / `not configured`）
- Regression riskとRollback / Recovery

CodeRabbit GitHub Appが導入済みなら、非Draft PRへの自動Reviewを待つ。未導入でもPR作成は妨げず、
`not configured` と正直に記録する。CodeRabbitの指摘はSourceで検証してから修正し、自動修正を無条件に適用しない。

## Phase 5: Self-Review Record（実装担当の説明記録）

Independent Reviewとは別に、実装担当としてPRへ説明Commentを残す:

```bash
gh pr review <PR番号> --comment --body "## 実装担当レポート
### ✅ 実装内容
### 🧪 実行した検証と結果
### 🔍 Independent Review
### ⚠️ リスク・注意点
### 📝 残課題"
```

このCommentはApproveではなく、実装と検証の証跡。最終判断は `/kio-review` と人間Ownerが行う。

## 完了報告フォーマット

```
✅ PR #<N> 作成完了
タイトル: <PRタイトル>
ブランチ: <impl_branch_prefix><numbers>
対象: closes #X[, closes #Y]
Verification: <実行結果の要約>
Independent review: <reviewer / rounds / final result>
CodeRabbit: <pending / clean / issues remain / not configured>
URL: https://github.com/...
```

## トークン節約ルール
- grep → Read の順序を守る
- `--json` + `-q` でgh出力を絞る
- `head -30` でTSエラーを打ち切る
- 同一ファイルの不要な再Readを避ける
- 複数issue操作は安全にまとめられる場合のみ1bashコマンドにまとめる
