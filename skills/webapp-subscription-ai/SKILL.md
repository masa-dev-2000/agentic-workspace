---
name: webapp-subscription-ai
description: 自分の Web アプリから、契約枠（Pro/Max/Team/Enterprise）の Claude を API 従量課金なしで使えるようにつなぐ方法。「AI を API 課金なしで組み込みたい」「自前の MCP を CI（GitHub Actions）から叩きたい」「画面のない実行環境から MCP に入りたい」ときに読む。
---

# Web アプリ × 契約枠の Claude（API 従量課金なし）

開発者向けの手順書。技術用語をそのまま使う。
利用者向けの画面には、ここに出てくる言葉（MCP・トークン・OAuth など）を出さないこと。

## いつ使うか

- 自分の Web アプリに「AI が文章を書く／内容を判断する」機能を足したいが、**生成 AI の API 従量課金を発生させたくない**
- すでに契約している Claude（Pro / Max / Team / Enterprise）の枠で動かしたい
- Worker やサーバーの中で AI API を呼びたくない（従量課金を出さない方針をとっている）
- 自前の MCP サーバーに、画面のない実行環境（CI）から接続したい

使わないほうがよい場合は最後の節に書いた。

## 考え方

**AI をアプリの中に置かない。** アプリは「依頼を順番待ちに積む」だけにして、
外の実行環境（GitHub Actions）で動く Claude Code が、MCP 経由で依頼を取りにきて、
書いたものを MCP 経由で戻す。契約枠で動く Claude Code が「書き手」になる。

```
  [利用者]
     │ 画面でボタンを押す
     ▼
  [Web アプリ (Worker)] ──① 依頼を drafts 表に queued で積む
     │                                   ▲                │
     │② repository_dispatch で起動を知らせる │④ submit_draft │③ list_draft_requests
     ▼                                   │                ▼
  [GitHub Actions]                    [MCP 入口 /agent-mcp]
     │  claude -p --mcp-config …           ▲  Bearer <通行証> で検証
     ▼                                     │  ツールは server 側でも絞る
  [Claude Code(契約枠)] ───────────────────┘

  画面は drafts の状態(queued / working / ready / failed)だけを見る。
  ⑤ 人が承認画面で承認 → はじめて外部へ公開される。
```

要点は3つ。

1. **課金の分離** … 実行は GitHub Actions、認証は `CLAUDE_CODE_OAUTH_TOKEN`。契約枠で動き API 従量課金は発生しない
2. **認証の分離** … 対話用の MCP 入口（OAuth 2.1）と、CI 用の入口（自前の長期トークン＝通行証）を分ける
3. **非同期前提** … 画面はキューに積むだけ。書き手を定型ロジックから AI に替えても画面は変えなくてよい

## 手順

### 1. 人にしかできない作業（依頼するのはここだけ）

一度に1つずつ頼む。

- **契約枠の鍵を作る**：ローカルで `claude setup-token` を実行する。1年有効の OAuth トークンが表示される
  （公式ドキュメント `code.claude.com/docs/en/authentication` の "Generate a long-lived token"）
- **GitHub の Secret に登録する**：`CLAUDE_CODE_OAUTH_TOKEN`
- **アプリの設定画面で通行証を発行し**、GitHub の Secret に登録する（この実装では `APP_RUNNER_TOKEN`）
- **公開 URL を GitHub の変数に登録する**（この実装では `vars.PUBLIC_URL`）
- **fine-grained PAT を作る**（アプリから即時起動したい場合のみ）。権限は **Contents: Read and write**（metadata read は自動で付く）。Worker 側の Secret に登録する（この実装では `GITHUB_DISPATCH_TOKEN`、対象リポジトリは `GITHUB_REPO`）

秘密情報の値は、コード・チャット・ドキュメント・ログのどこにも書かない。書いてよいのは名前まで。

### 2. アプリ側に用意するもの

- キューの表（`queued` / `working` / `ready` / `failed`）。この実装では `drafts`
- CI 専用の MCP 入口。この実装では `/agent-mcp`（アプリ側のルート定義）
- 通行証の発行・検証
- MCP のツール絞り込み（`src/mcp/server.ts` の `McpScope`）
- 起動の知らせ（`src/service/drafts.ts` の `notifyRunner`）

### 3. ワークフローを置く

次節の YAML を `.github/workflows/` に置く。指示文は別ファイルにする。

## 最小の動くワークフロー例

実際に動かして検証した構成を、最小限に縮めたもの。

全文は [`references/workflow-example.yml`](references/workflow-example.yml) にある（そのまま `.github/workflows/` に置ける）。
要点だけ抜き出すと次のとおり。

```yaml
- run: curl -fsSL https://claude.ai/install.sh | bash   # Claude Code を入れる
- env:
    CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}   # 契約枠で動く
  run: |
    claude -p "$(cat .github/run/draft-prompt.txt)" \
      --mcp-config "${RUNNER_TEMP}/mcp.json" \
      --allowedTools "mcp__app__list_requests,mcp__app__submit_result" \
      --max-turns 20 --output-format json
```

- **`--bare` は付けない。** 付けると `CLAUDE_CODE_OAUTH_TOKEN` を読まず、契約枠で動かない
- **指示文は本文に埋めず別ファイルにする。** YAML のブロック内でヒアドキュメントの終端が行頭に戻ると書式が壊れる

補足。

- ツール名は `mcp__<サーバー名>__<ツール名>`。サーバー名は `--mcp-config` の `mcpServers` のキー（例では `app`）
- `--output-format json` にすると `.result` で最終出力を取り出せる
- 結果をリポジトリへ書き戻すなら `permissions: contents: write` が要る

### 公式 Action（`anthropics/claude-code-action`）を使わない理由

受け付けるイベントが実装で限られており、**`push` は含まれない**（`Unsupported event type: push`）。
`src/github/context.ts` に一覧がある。

- `ENTITY_EVENT_NAMES` = issues / issue_comment / pull_request / pull_request_review / pull_request_review_comment
- `AUTOMATION_EVENT_NAMES` = workflow_dispatch / repository_dispatch / schedule / workflow_run

公式ドキュメントには "any GitHub event" とあるが、実装はこの一覧に限られる（記載のほうが広い）。
`repository_dispatch` / `schedule` / `workflow_dispatch` だけでよいなら公式の部品でも成立する。
`push` から自分で起動したい場合は `claude -p` を直接実行する。

## アプリからの起動

キューに積んだ直後に GitHub へ知らせる。

```
POST https://api.github.com/repos/{owner}/{repo}/dispatches
Authorization: Bearer <PAT>
Accept: application/vnd.github+json
X-GitHub-Api-Version: 2022-11-28
body: {"event_type": "draft-requested"}
```

実装は `src/service/drafts.ts` の `notifyRunner`。**失敗しても致命的にしない**。
キューには積まれているので、`schedule`（5分ごと）が拾い直す。
PAT 未設定でも動く設計にしておくと、設定前でも機能が壊れない。

注意：`repository_dispatch` と `schedule` は**既定ブランチのワークフローしか動かない**。
作業ブランチでは試せない。

## MCP 側に用意するもの（設計方針）

### 入口を分ける

- 対話用（`/mcp`）… OAuth 2.1。ブラウザで許可を押す。**画面のない CI からは使えない**。アクセストークンは既定1時間で切れ、CI は更新できない
- CI 用（`/agent-mcp`）… `Authorization: Bearer <通行証>` を自前で検証する

OAuth の有効期間を延ばして共用するのは避ける。対話側の安全性まで下がる。

### 通行証

設計方針は次のとおり。

- 値は保存しない。**SHA-256 のハッシュだけ**を保存し、照合はハッシュで行う
- 発行時に**1回だけ**画面へ表示する
- 期限つき（この実装は90日）。取り消せる。発行し直すと古いものは無効
- 最終利用日時を記録し、設定画面に出す（使われていないことに気づけるようにする）

### ツールの絞り込み（二重防御）

**クライアントの `--allowedTools` は相手側の設定にすぎない。サーバー側でも必ず絞る。**

この実装では `McpScope`（`"full" | "draft"`）で、CI からの接続には
文章づくりの2つ（`list_draft_requests` / `submit_draft`）しか `registerTool` しない。
公開・投稿にかかわるツールは**そもそも登録されない**ので、名前を知っていても呼べない。

自動テストで「CI からは投稿系のツールが見えないこと」「隠したツールを直接呼んでも使えないこと」を固定しておく。

### 非同期前提の設計

AI の応答には数十秒〜かかる。画面を止めて待たせない。

- 画面は「1件積む」だけ。押した瞬間に「つくっています」に切り替える
- 状態は `queued` / `working` / `ready` / `failed` の4つ
- 待たずに離れてよいと明示し、できたらホームで知らせる
- 二重に書かれないよう、書く前に条件付き UPDATE で1回だけ確保する
- 自動読み直しは `<meta http-equiv="refresh">` でよい（JavaScript に頼らない）

こうしておくと、書き手を定型ロジックから AI に替えても**画面は1行も変えなくてよい**
（「書き手」をインターフェースで抽象化しておくと、定型ロジック → AI の差し替えが設定1つで済む）。

## 費用の考え方

| もの | 費用 |
| --- | --- |
| Claude 本体（`CLAUDE_CODE_OAUTH_TOKEN` 経由） | **契約枠。API 従量課金は発生しない** |
| GitHub Actions | パブリックリポジトリは無料。プライベートは無料枠の分数を消費する |
| Cloudflare Workers / D1 / R2 / KV | 無料枠の範囲で動かす |
| 生成 AI の API（Anthropic / OpenAI など） | **使わない**。使えば従量課金 |

契約枠には利用量の上限がある（プランごとに異なる）。
`schedule` を短くしすぎると、依頼が無くても毎回 Claude が起動して枠を消費する。
「依頼が0件なら即終了する」と指示文に書いておくこと（`draft-prompt.txt` の手順2がそれ）。

## 安全の考え方

- トークンは Secret に入れる。ワークフローでは環境変数からファイルへ書くだけにし、`echo` しない
- PAT の権限は最小に。`repository_dispatch` に必要なのは Contents: Read and write だけ
- 通行証は期限つき・取り消し可・ハッシュ保存
- **サーバー側でツールを絞る**（クライアント設定だけに頼らない）
- 外部への公開・投稿は、必ず人が承認画面で承認してからにする。会話上の「投稿して」は承認とみなさない
- 監査ログを残す（誰が・いつ・何をしたか。秘密情報は入れない）

## 落とし穴と対処

| 落とし穴 | 対処 |
| --- | --- |
| YAML のブロックスカラー内にヒアドキュメントを書くと、終端行が行頭に戻った時点でブロックが終わり、「ジョブ0件」で失敗する | **長い指示文は別ファイルに切り出す**（`.github/run/draft-prompt.txt`）。`claude -p "$(cat …)"` で読む |
| ワークフローの構文チェックが対象ファイルを決め打ちで、新規ファイルが点検から漏れた | `.github/workflows` を走査する形にする |
| `anthropics/claude-code-action` が `push` で `Unsupported event type: push` | `claude -p` を直接実行する |
| `--bare` を付けると `CLAUDE_CODE_OAUTH_TOKEN` を読まない | **`--bare` は使わない** |
| GitHub MCP の `actions_run_trigger` が 403 | `paths` フィルタつきの `push` トリガでワークフローを起動する |
| `repository_dispatch` / `schedule` が作業ブランチで動かない | 既定ブランチへ取り込んでから試す |
| 同じ依頼を二重に処理する | `concurrency` でワークフローを直列化し、DB 側でも条件付き UPDATE で確保する |

## この方法を選ばないほうがよい場合

- **同期で即応答が要る**（数秒以内に画面へ返す必要がある）。CI の起動だけで数十秒かかる
- **常時稼働・高頻度**。Actions の起動回数と契約枠の利用量が現実的でなくなる
- **不特定多数の利用者に提供する**。契約枠は契約者本人の利用が前提。1人・少人数向けの仕組み
- **秒単位の SLA や失敗時の即時リトライが要る**。ここでのリトライは `schedule` の5分待ち
- **AI の応答を厳密に監視・課金按分したい**。API のほうが計測しやすい

## 実際に動かした例

`masa-dev-2000/update_gbp`（Google ビジネスプロフィール投稿支援）で、この方法を実装して動作を確認した。
対応するファイルは次のとおり。

| ここでの説明 | 実物 |
| --- | --- |
| ワークフロー | `.github/workflows/draft.yml` / `.github/run/draft-prompt.txt` |
| 通行証の発行・検証 | `src/auth/runner.ts` |
| CI 専用の MCP 入口 | `src/app.ts` の `/agent-mcp` ルート |
| サーバー側のツール絞り込み | `src/mcp/server.ts` の `McpScope` |
| 順番待ちができたときの通知 | `src/service/drafts.ts` の `notifyRunner` |
| 書き手の差し替え口 | `src/draft/` |
| 判断の記録 | `docs/DECISIONS.md` の ADR-0012 / ADR-0014 / ADR-0015 |
| 踏んだ落とし穴 | `docs/ISSUES.md` の ISSUE-014 / ISSUE-015 |
