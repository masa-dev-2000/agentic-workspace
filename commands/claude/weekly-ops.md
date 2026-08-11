週次の運用サイクル（人間起点の判断系タスクのみ）を固定順序で実行する。

## 前提

決定論的チェック（health_check.py）は Task Scheduler（`agentic-weekly-health` タスク）で
自動実行される。このコマンドは自動化しない/できない **LLM判断が要る半分** だけを担う。
このコマンドは PR をマージしない。criteria を activate しない。それらは常に人間ゲート。

## 手順（この順番で実行する）

### Step 0: intake のドレイン

`status:needs-triage` ラベルの付いた issue を列挙する。

```bash
gh issue list --state open --label "status:needs-triage" --json number,title,labels,body --limit 100
```

1件以上あれば issue-ledger エージェントへ渡す（`agents/claude/issue-ledger.md` の
「Triage」セクションの手順に従わせる: evidence検証 → 重複排除 → criteria採点 →
`status:needs-triage` ラベルの置き換え、または却下）。0件ならその旨を報告して次へ進む。

### Step 1: 直近の health report を表示

`%USERPROFILE%\.claude\health\latest.md` を読み、そのまま提示する。ファイルが存在しない
場合は「health report がまだ存在しない（agentic-weekly-health タスクが未実行、または
health_check.py が一度も走っていない）」と明示的に述べる。推測で埋めない。

### Step 2: criteria カバレッジ（最優先シグナル）

`needs-criterion` ラベルの付いたオープンissueを列挙する。

```bash
gh issue list --state open --label "needs-criterion" --json number,title,body --limit 100
```

現時点ではオープンissue全6件がこのラベルに該当する。列挙した各issueを criteria-steward
エージェントへ渡し、判断軸（判断軸が無い issue への axis 定義）を検討させる。
criteria-steward は `proposed` を作るだけで `active` にはしない — activate は人間の承認が必要。

### Step 3: issue-finder の週次ローテーションスイープ

lens は ISO週番号で決定論的にローテーションする（選択ではない）:

```
week % 4 == 0 → validation
week % 4 == 1 → drift
week % 4 == 2 → docs
week % 4 == 3 → tooling
```

ISO週番号の取得:

```bash
python -c "import datetime; print(datetime.date.today().isocalendar().week)"
```

決定した lens を issue-finder エージェントへ渡し、スコープを本ワークスペース
（agentic-workspace リポジトリ）に限定してスイープさせる。

### Step 4: 候補を issue-ledger へ

Step 3 の issue-finder が返した候補をそのまま issue-ledger エージェントへ渡し、
file / merge-into-existing / reject の判定をさせる。

### Step 5: agent-steward ロースター監査 — 四半期のみ

```
week % 13 == 1 のときだけ実行する
```

それ以外の週はスキップし、その旨を一言報告する。

**なぜ毎週やらないか（変更しないこと）**: 6エージェントの小さなロースターを毎週監査しても
新しいシグナルはほとんど出ず、`validator-signal-hygiene`（アクティブ criterion）が警告する
「常に同じ出力を垂れ流す warn channel は新しい警告を見えなくする」というノイズをそのまま
生む。四半期カデンスはこの criterion に整合させた意図的な設計であり、「毎週回した方が
安全では」と直しにいかないこと。

条件を満たす週は agent-steward エージェントへロースター監査を依頼する
（`agents/claude/agent-steward.md` の Audit 責務どおり: description重複、tools逸脱、
使用実績ゼロのエージェントなど）。

### Step 6: 対策の有効性確認（期日到来分）

ミス防止ルールブックの星取表で、検証方法が決まっているのに結果が空欄の行を列挙する。

```bash
python -X utf8 -c "
import openpyxl, pathlib
p = pathlib.Path.home()/'dev/00_work/00_ops-rulebook/ミス防止ルール星取表.xlsx'
ws = openpyxl.load_workbook(p).active
hdr = [ws.cell(4,c).value for c in range(1, ws.max_column+1)]
im, ir = hdr.index('検証方法')+1, hdr.index('検証期日／結果')+1
for r in range(5, ws.max_row+1):
    rule, method, result = ws.cell(r,1).value, ws.cell(r,im).value, ws.cell(r,ir).value
    if rule and method and not result:
        print(f'未検証: {rule} / 方法={method}')
"
```

出力があれば、対策ごとに検証方法（再現テスト／発火証跡／期間観測）に従って**実際に確認する**。
再現テストは、実際に同じ失敗を起こして対策が止めるかを見る。結果を該当セルへ
`YYYY-MM-DD 有効／無効／判定不能` の形で記入する。打ちっぱなしの対策を残さないことが目的。
判定が「無効」なら、RULEBOOK.md の対策の型に従って強度を上げ直す（S2→S1 等）。

### Step 7: 自動判断のレビュー（精度向上ループ）

未レビューの自動判断を、確信度の低い順に列挙する。

```bash
python -X utf8 C:/Users/masa/dev/agentic-workspace/scripts/decision_stats.py --pending
```

各判断について、ユーザーに「同意 / 不同意（+ 正しい答え）」を聞く。**確信度=低のものを優先**し、
高いものは件数が多ければ抜き取りでよい。結果は該当issueのコメント内 `review:` を
`agree` または `disagree: <正しい答え>` に書き換えて記録する。

**不同意は必ず次の3つのどれかに落とす。「AIが間違えた」で終わらせない**（判断軸
`decision-risk-levels` の要求）:

1. 判断軸が無かった → criteria-steward に起草させる
2. 判断軸の解釈がずれた → その判断軸に反例を追記させる
3. リスク分類が甘かった → その判断クラスを L1→L2 に降格する提案を出す

最後に一致率と昇格・降格の推奨を確認する。

```bash
python -X utf8 C:/Users/masa/dev/agentic-workspace/scripts/decision_stats.py
```

昇格・降格自体は L3（人間が実行）。推奨が出ても、このコマンドでは実行しない。

## このコマンドがやらないこと

- PR のマージ（`/kio-devmerge` `/kio-mainmerge` の領域）
- criteria の activate（人間の明示承認が必要）
- agent-steward の hire/activate（人間の明示承認が必要）
- health_check.py 自体の実行（Task Scheduler の `agentic-weekly-health` タスクが担当）

## 最後に

Step 0〜7 それぞれの結果（件数・渡した先・スキップした場合はその理由）を1つのレポートに
まとめて提示する。
