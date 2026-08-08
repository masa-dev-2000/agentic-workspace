---
name: work-closeout-report
description: 作業終了時に、検証済みのオーケストレーター／検証エビデンスを「実施・完了・注意・あなたに必要」の4ブロックへ圧縮して報告する。Use when a task, plan, implementation, or verification run needs a concise evidence-bound closeout report.
---

# Work Closeout Report

作業終了時に、`closeout_v1` handoffを人間向けの固定4ブロックへ整形する。完了判定、検証、状態変更、承認、telemetry本文保存は担当しない。

## Workflow

1. `adaptive-orchestrator`のreport stageから`closeout_v1`を受け取る。
2. `plan_digest`と`attempt_id`が現在のジョブと一致することを確認する。不一致、期限切れ、証拠欠落は報告を拒否する。
3. `progress-verifier`が供給した`completion_state`とevidence refsだけを使う。自己申告、ファイル存在、GAN verdictだけで完了を作らない。
4. `human-task-requester`のpending actionsとapproval queue refsを「あなたに必要」へ移す。`approval_requested`と`approved`を混同しない。
5. optionalな`decision_support`を根拠付きで受け取る。各配列は最大3件、unknown key/body/過長値は拒否する。
6. 4ブロックの外側先頭に、`work_status`を写像した状態行を1行だけ出力する。これは表示であり、状態の再判定・昇格・権限付与ではない。

```text
状態: <計画中|実装中|検証中|完了|要対応|ブロック>
実施: <何をした>
完了: <検証済み成果・証拠>
注意: <未解決・制約・失敗・期限>
あなたに必要: <承認・判断・人間作業。なければ「なし」>
```

`work_status`は`adaptive-orchestrator`だけが供給し、closeoutは再判定・昇格しない。機械enumを
日本語表示へ写像する: `planning`→計画中、`implementing`→実装中、`verifying`→検証中、
`completed`→完了、`attention_required`→要対応、`blocked`→ブロック。`completed`
は既存のcompletion evidenceが成立している場合だけ許容し、closeout側で`partial`や
`unknown`を昇格させない。`waiting_human`または`approval_requested`→`blocked`、
stale単独→`attention_required`、digest/attempt mismatch→拒否または`blocked`、
`unknown`→`blocked`とする。旧handoffに`work_status`がない場合はstage_statusesと
completion_stateから決定論的に導出し、判定不能なら`blocked`にする。

証拠が不十分なら、完了ではなく`unknown`または`blocked`として注意に示す。推測、長いログ、ツール本文、重複した作業提案は出さない。各ブロックは簡潔にし、次アクションは最大1件にする。

`waiting_human`は`blocked`、stale単独は`attention_required`、digest/attempt mismatchは拒否または`blocked`、unknownは`blocked`へ写像し、いずれも`complete`にしない。`next_action`はローカルで解決可能なopaque refと短いlabelを最大1件だけ受け取る。

`decision_support.materials`と`hypotheses`は「注意」へ、`recommendations`と
`decision_support.next_actions`は「あなたに必要」へ表示する。actorは`human`→あなたに
必要、`ai`→次の自動処理、`external`→外部依頼へ写像し、不明値は拒否または`blocked`とする。
`approval_requested`は`blocked`と表示し、recommendationの`approved`はapproval evidence
がある場合だけ表示する。closeoutは生成・再判定・
承認昇格を行わず、根拠と状態をそのまま圧縮する。旧`next_action`は単一項目として
`next_actions`へ決定論的に取り込む。

## Safety and privacy

入力は`references/schema.md`のtyped metadata allowlist/length契約に従う。本文、prompt、response、tool outputは保存・転送せず、opaque refsのみ扱う。raw scanが失敗した場合は詳細をfail-closedで省略する。`strict`は権限や承認ではなく、approval-required操作は別のhuman approval handoffへ送る。

## Handoff ownership

- `adaptive-orchestrator`: job、attempt、stage、plan digest、実行状態を所有
- `progress-verifier`: completion stateとcompletion evidenceを所有
- `human-task-requester`: 人間作業・承認依頼の構造化を所有
- `work-closeout-report`: 表示形式だけを所有
- `skill-telemetry`: body-free lifecycle/outcome記録を所有

評価ケースは[references/eval-cases.md](references/eval-cases.md)を使用する。
