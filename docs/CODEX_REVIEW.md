# Codex ネイティブ・コードレビュー運用

## 目的

CodeRabbitを追加せず、Codexを次の2か所で独立レビュアーとして使う。

1. **PR前**: Codexの`/review`で、作業ブランチを`main`との差分として読む。
2. **PR後**: GitHub上のCodex Code Reviewで、重大な回帰だけを別コンテキストから確認する。

最終判断とMergeは人間が行う。レビューAgent自身による自動Mergeは行わない。

```text
実装Codex
   ↓
実テスト・validator
   ↓
別レビュアーの /review（read-only）
   ↓
Pull Request
   ↓
GitHub Codex Code Review（P0/P1中心）
   ↓
人間が方針・リスクを確認
   ↓
Merge
```

## リポジトリ側の構成

- ルート`AGENTS.md`
  - Codexが全変更に適用するリポジトリ共通指示。
  - `## Code Review Rules`には、このリポジトリ固有の重大な境界だけを置く。
- `skills/.system/review-agent/`
  - 明示的に呼ぶ、provider-neutralなread-onlyレビューSkill。
  - P0〜P3のうち、変更によって新たに発生した具体的・修正可能な欠陥だけを報告する。
- `.github/workflows/validate.yml`
  - validatorとunit testを決定論的に実行する。Lintや形式検査はCodexのレビュー規則へ重複させない。
- `.github/pull_request_template.md`
  - 実行した検証、独立レビュー、残存リスクをPR本文に残す。

## 1回だけ必要なCodex側設定

この設定はGitHubファイルではなく、ChatGPT/CodexのRepository settingsで行う。

1. Codex Cloudへ`masa-dev-2000/agentic-workspace`を接続する。
2. Codex settingsで、このRepositoryの**Code review**を有効にする。
3. **Automatic reviews**を有効にする。
4. Security Reviewは、Credential・認証・外部通信・権限・削除処理など高リスク変更で必要に応じて有効にする。

公式手順:

- GitHub Code Review: `https://learn.chatgpt.com/docs/third-party/github`
- ローカル`/review`: `https://learn.chatgpt.com/docs/code-review`

## 日常の流れ

### 1. 実装と検証

実装Agentは、変更後に少なくとも次を実行する。

```bash
python -X utf8 scripts/validate_workspace.py --no-live
python -X utf8 -m unittest discover -s scripts/tests -v
```

失敗した検証を成功扱いしない。実行できなかった場合は理由と未確認範囲をPRへ書く。

### 2. PR前の独立レビュー

Codex App / CLI / IDEで`/review`を実行し、**Review against a base branch**で`main`を選ぶ。

- 実装時の会話ではなく、専用レビュアーに差分を読ませる。
- Reviewerは作業ツリーを変更しない。
- P0/P1はPR作成前に直す。
- P2/P3は修正するか、残す理由をPRの「残存リスク」に書く。
- 修正後は差分全体をもう一度レビューする。

明示的なSkillを使う場合は、`$review-agent`へ対象を指定する。

### 3. GitHub上のCodexレビュー

Automatic reviewsが有効なら、PRをレビュー可能な状態にした時点でCodexがレビューする。
手動実行はPRコメントへ次を書く。

```text
@codex review
```

一度だけ焦点を追加する例:

```text
@codex review for regressions in wiring, data locality, and validator enforcement
```

GitHub上の通常Code Reviewは、ノイズを抑えるためP0/P1を中心に投稿する。より広いP2/P3確認はPR前の`/review`で行う。

### 4. 指摘への対応

1. 指摘の再現条件と該当経路を確認する。
2. 実装Agentへ最小修正と回帰テストを依頼する。
3. validator・unit testを再実行する。
4. `@codex review`を再実行する。

Codexに同じPR上で修正させる場合も、オーナーが修正内容を明示的に指定する。

```text
@codex fix the P1 issue about <具体的な問題>
```

Review結果を理由に、Codexが無断で修正・Mergeする運用にはしない。

## Merge条件

次をすべて満たしてから人間がMergeする。

- CIが成功している。
- GitHub Codex ReviewのP0/P1が0件、またはオーナーが理由付きで受容している。
- 変更目的と実装方針を人間が理解している。
- 残存リスクとRollback方法がPRに書かれている。
- Customer data、Credential、Ledgerなどが共有Repositoryへ混入していない。

## 意図的に採用しないもの

- CodeRabbit
- OpenAI API keyをRepository secretへ置く独自Review Action
- Review Agentによる自動Merge
- Lint・format・schema検証を自然言語レビューへ移すこと
- 実装Agentの自己申告だけで「レビュー済み」とすること

ネイティブGitHub Reviewは重大回帰の常設検査、`/review`はPR前の広い独立検査、CIは決定論的検査、人間は事業要件とリスク受容を担当する。

## 初回スモークテスト

この運用を追加するPR自体を代表例として使う。

1. PRを作成する。
2. PRへ`@codex review`とコメントする。
3. Codexが目のリアクションを付け、GitHub Reviewを投稿することを確認する。
4. 反応しない場合は、Codex Cloud接続とRepositoryのCode review設定を確認する。
5. 指摘の有無にかかわらず、CIと人間のレビューを通してからMergeする。
