---
name: api-cost-report
description: 作業が一区切りついたとき、その作業を Anthropic API で流していたらいくらだったかを実測トークンから算出して報告する。「いくらかかった」「コスト」「API換算」と言われたとき、または大きめの作業を終えたときに使う。
---

# API 換算コストの報告

サブスクの実請求額ではない。**同じトークンを API で流したらいくらか**の換算値を出す。
トークン数は Claude Code のトランスクリプト（`~/.claude/projects/*/*.jsonl`）に残る
`usage` を合算するだけで、推定はしない。

## 使い方

```bash
# 直近の作業(このセッション、今から遡ってN時間)
python ~/.codex/skills/api-cost-report/scripts/api_cost.py --session <セッションID> --hours 3

# セッション全体
python ~/.codex/skills/api-cost-report/scripts/api_cost.py --session <セッションID>

# プロジェクト全体(そのプロジェクトの全セッション。サブエージェントの分も入る)
python ~/.codex/skills/api-cost-report/scripts/api_cost.py

# 機械可読
python ~/.codex/skills/api-cost-report/scripts/api_cost.py --session <ID> --json
```

Windows の PowerShell/cmd で文字化けするときは `PYTHONIOENCODING=utf-8` を付ける。

主なオプション:

| オプション | 意味 |
|---|---|
| `--session <ID>` | セッションID。指定しなければプロジェクト全体 |
| `--hours N` / `--since <ISO8601>` | 期間を絞る。「この一区切り分だけ」を出すときに使う |
| `--intro` | Sonnet 5 の導入価格（$2/$10、2026-08-31まで）で計算する |
| `--rate` | USD→JPY の換算レート（既定 155） |

セッションIDは、この会話のトランスクリプトのファイル名（拡張子なし）。
worktree ごとに別ディレクトリへ落ちることがあるが、スクリプトが横断で探す。

## 報告のしかた

1. スクリプトを走らせる（`--hours` で区切りの範囲に合わせる）
2. **合計金額を最初の1文で言う**。内訳はその後
3. 金額を大きくしている要因を1つだけ指摘する。ほぼ常に **cache read** か **output** のどちらか

報告の型:

> この一区切り（約3時間）を API で流していたら **$12.40（約1,900円）**。
> 内訳は Opus 5 が 210 回、うち大半はキャッシュ読み出し 38M トークン。
> 出力は 41k トークンしか出していないので、金額はほぼ文脈の読み直しコスト。

やらないこと:

- サブスクの請求額として報告しない。必ず「API で流していたら」と書く
- 単価表を毎回貼らない。聞かれたら答える
- モデル別の全内訳を並べない。合計と、金額を作っている1要因だけ

## 単価とキャッシュ倍率

`scripts/api_cost.py` の `PRICES` に持っている（USD / 100万トークン）。
Fable 5 $10/$50、Opus 5 系 $5/$25、Sonnet 5 $3/$15（導入 $2/$10）、Haiku 4.5 $1/$5。

キャッシュは入力単価に対する倍率で計算する: 読み出し 0.1x、
書き込みは TTL で 5分=1.25x / 1時間=2x。トランスクリプトの
`usage.cache_creation.ephemeral_{5m,1h}_input_tokens` で TTL を判別している。

**モデルが増えたら `PRICES` に足す。** 未登録のモデルは金額に含めず、
「単価未登録のため除外」として名前を出す（黙って 0 円にしない）。
最新の単価は `claude-api` skill の Current Models 表、または
https://claude.com/pricing で確認する。
