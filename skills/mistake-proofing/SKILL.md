---
name: mistake-proofing
description: Apply the global mistake-prevention rulebook (C:\Users\masa\dev\00_work\00_ops-rulebook) across all projects. Use when the user reports a mistake or near-miss, or asks for 再発防止・なぜなぜ分析; before deleting or cancelling anything something else may depend on - an account, service, subscription, repository, directory, database, or file set - including local deletions; before a structural change to a live system, workflow, or external commitment (migration, cutover, restructuring); when submitting documents to external parties (行政・顧客); when a deadline-bound procedure is received; and as a final gate before handing over a client-facing, regulatory, sales, or outbound-email document. Runs two-track (発生系/流出系) why-why analysis, checks the dependency ledger before irreversible actions, writes the impact list before a structural change, and updates the 星取表. Do not use for Claude tool-execution failures (use failure-loop-guard or failure-learning).
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
   - **ミス・事故の報告を受けたら、まず等級を判定する**（RULEBOOK R4）。**外に出ていない＝軽微／出たが取り返せた＝中／外に影響が残った・取り返せない＝重大。迷ったら上の等級で扱う。**
     - **軽微・中**：`incidents/near-miss-ledger.md` に**1行**追記する。**なぜなぜ2系統・構造・対策・反映先まで必ず埋める**（対策の空欄は禁止。打たないと決めたなら受容した理由を書く）。止血・一報は該当なしと判定するだけで調査しない。記録ファイルは作らない。
       台帳末尾の件数表を必ず確認し、**同じ構造の2件目なら対策の強度を1段上げる（S3→S2、S2→S1）。3件目は重大として扱う。**ルール・手順の変更に至ったものは記録ファイルへ昇格させ台帳からリンクする
     - **重大**：`INCIDENT-RESPONSE.md` を開き、止血→一報→記録→対策→水平展開→検証→クローズ。**被害が広がっている最中に記録を書き始めない。**時計で縛るのは一報だけ（その日のうち）
   - **なぜなぜ分析は等級に関係なく全件行う**（軽微は台帳の行内に1〜2段でよい）。発生系/流出系の2系統で、「注意不足」で止めず仕組みの欠陥まで掘る。ヒアリングは1問ずつ。**削るのは手続きであって分析ではない。**
   - 削除・解約の依頼を受けたら、**実行より先に**依存台帳（上記パス。無ければ案件フォルダを探す）と星取表を照合し、壊れる外部約束を提示する。依存が残る場合は移管完了まで削除を止め、即削除でなく停止を提案する。
   - **外部に出る文書（クライアント資料・行政提出物・営業資料・送信メール）を作成／改訂したら、渡す前にR1bの5点を通す。**自分が書いた文書も必ず通す。断定した記述について**一次資料または確認相手・日付を言えるか**を自問し、言えないものは書き換えるか落とす。
     **制度・法令に触れる記述は、一次資料とその訂正履歴を実際に開いて突き合わせる**（AI研修なら `design/subsidy/subsidy-findings-*.md`）。**他の自社資料と一致していることを根拠にしない**——誤りが複数資料に伝播していると内部整合では検出できない（2026-08-11の検証で実際に見逃した）。
     通した結果（引っかかった点と処置）をユーザーに報告する。
3. 対策は RULEBOOK.md の「対策の型」に従う。S1（仕組み）から検討し、S1にしないなら降格理由を記録してからS2へ降りる。「気をつける」単独は受理しない。**検証方法と検証期日を必ず決める**（S1は再現テスト必須）。失敗の主体が人でもAIでも型は同じ。
   - **対策を書いたら、`references/countermeasure-wiring.md` の接続表でそれが次に作動する瞬間まで降ろす。**工程名で止めず「何を開くか・何と突き合わせるか・何が残るか」を埋める。埋まらない欄は記入漏れではなく対策の設計不足として扱い、対策を書き直す。外部へ再発防止を説明する場合も同じ表から書く。
4. 終了時に必ず水平展開を1回問う：「同じ構造を持つ他の業務はないか」。候補があれば星取表に行を追加する。AI環境も列の1つなので、業務側の対策がAIの操作にも要るかを併せて見る。
5. 変更・追記した資産（記録・台帳・星取表）のパスを報告して完了とする。

## 場面→ルール対応

| 場面 | ルール |
|------|--------|
| 外部への提出・約束（URL・口座・日付等の揮発情報を含む） | R1 台帳登録 |
| **外部に出す文書の中身が固まった（配布・送信・提出の直前）** | **R1b 出す直前のゲート（5点）** |
| アカウント・サービス・データの削除／解約 | R2 削除前照合 |
| 期限つき手続きの発生 | R3 逆算チェック登録 |
| ミス・ヒヤリの報告、再発防止依頼 | R4 incident記録・水平展開 |
| 大きな変更 | R5 ミニCAB・事後レビュー |

| AI研修の案件工程（受注・計画届・各回・変更・支給申請） | AI研修 `WORKFLOW.md` §2 のトリガー起動表へ回す |

## 完了条件

- 該当ルールの成果物（incident記録／台帳の行／星取表の更新／照合記録）が実ファイルとして存在し、パスを報告済みであること。
- R2では照合結果をユーザーが確認してから削除に進んでいること（照合前の削除実行は不可）。
- R4では**等級を問わず、なぜなぜ2系統と対策1つ（反映先つき）が書かれていること。**軽微でも分析だけで終わらせない。対策欄の空欄は未完了とみなす。
- R4の重大では止血と一報が済んだことを確認してから記録に進んでいること（記録を先に書き始めない）。
- R1bでは、断定した記述それぞれについて出典（一次資料／確認相手・日付）を示せているか、示せないものを落としたか、契約書との突き合わせ結果、および**宛先・提出先が正しいことの確認**を報告していること。
