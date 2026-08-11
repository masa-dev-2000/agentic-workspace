---
name: mistake-proofing
description: Apply the global mistake-prevention rulebook (C:\Users\masa\dev\00_work\00_ops-rulebook) to prevent and learn from business mistakes across all projects. Use when the user reports a mistake, near-miss, or asks for 再発防止・なぜなぜ分析; before deleting or cancelling an account, service, or subscription; when submitting documents or information to external parties (行政・顧客); or when a deadline-bound procedure is received. Records incidents with two-track (発生系/流出系) why-why analysis, checks the dependency ledger before irreversible actions, registers volatile external commitments, and updates the horizontal-deployment scorecard (星取表). Do not use for Claude tool-execution failures (use failure-loop-guard or failure-learning instead) or for drafting the external documents themselves.
---

# Mistake Proofing（ミス防止ルールブック執行）

正本はルールブック側にある。このスキルは検知と執行のみを担い、内容を複製しない。

- ルール本体: `C:\Users\masa\dev\00_work\00_ops-rulebook\RULEBOOK.md`
- **事故発生後に進める順序**: 同 `INCIDENT-RESPONSE.md`
- テンプレ: 同 `templates\incident-template.md` / `templates\依存台帳-template.md`
- 星取表（全事業共通）: 同 `ミス防止ルール星取表.xlsx`

案件側の実物（AI研修の場合。他事業は同等のものを探す）
- 依存台帳: `dev\00_work\02_Yui\ai-training\design\operations\dependency-ledger.md`
- 実行ワークフロー・入口: 同 `WORKFLOW.md` → `case-runbook.md`
- 星取表（AI研修）: 同 `dryrun-2026-08.md` §2

## 手順

1. 発動場面を特定し、RULEBOOK.md の該当ルール（R1〜R5）を読む。
2. ルールの手順に従い、テンプレートから実物（incident記録・依存台帳）を作成・更新する。
   - **ミス・事故の報告を受けたら、記録より先に `INCIDENT-RESPONSE.md` を開き、その順序に従う。**
     止血→一報→記録→対策→水平展開→検証→クローズ。**被害が広がっている最中に記録を書き始めない。**
     時計で縛るのは一報だけ（その日のうち）。他は前段階の完了で次に進む。
   - incident記録はなぜなぜ分析を発生系/流出系の2系統で行い、「注意不足」で止めず仕組みの欠陥まで掘る。ヒアリングは1問ずつ。
   - 削除・解約の依頼を受けたら、**実行より先に**依存台帳（上記パス。無ければ案件フォルダを探す）と星取表を照合し、壊れる外部約束を提示する。依存が残る場合は移管完了まで削除を止め、即削除でなく停止を提案する。
3. 対策は RULEBOOK.md の「対策の型」に従う。S1（仕組み）から検討し、S1にしないなら降格理由を記録してからS2へ降りる。「気をつける」単独は受理しない。**検証方法と検証期日を必ず決める**（S1は再現テスト必須）。失敗の主体が人でもAIでも型は同じ。
4. 終了時に必ず水平展開を1回問う：「同じ構造を持つ他の業務はないか」。候補があれば星取表に行を追加する。AI環境も列の1つなので、業務側の対策がAIの操作にも要るかを併せて見る。
5. 変更・追記した資産（記録・台帳・星取表）のパスを報告して完了とする。

## 場面→ルール対応

| 場面 | ルール |
|------|--------|
| 外部への提出・約束（URL・口座・日付等の揮発情報を含む） | R1 台帳登録 |
| アカウント・サービス・データの削除／解約 | R2 削除前照合 |
| 期限つき手続きの発生 | R3 逆算チェック登録 |
| ミス・ヒヤリの報告、再発防止依頼 | R4 incident記録・水平展開 |
| 大きな変更 | R5 ミニCAB・事後レビュー |

| AI研修の案件工程（受注・計画届・各回・変更・支給申請） | AI研修 `WORKFLOW.md` §2 のトリガー起動表へ回す |

## 完了条件

- 該当ルールの成果物（incident記録／台帳の行／星取表の更新／照合記録）が実ファイルとして存在し、パスを報告済みであること。
- R2では照合結果をユーザーが確認してから削除に進んでいること（照合前の削除実行は不可）。
- R4では止血と一報が済んだことを確認してから記録に進んでいること（記録を先に書き始めない）。
