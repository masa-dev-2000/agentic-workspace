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

## このコマンドがやらないこと

- PR のマージ（`/kio-devmerge` `/kio-mainmerge` の領域）
- criteria の activate（人間の明示承認が必要）
- agent-steward の hire/activate（人間の明示承認が必要）
- health_check.py 自体の実行（Task Scheduler の `agentic-weekly-health` タスクが担当）

## 最後に

Step 0〜5 それぞれの結果（件数・渡した先・スキップした場合はその理由）を1つのレポートに
まとめて提示する。
