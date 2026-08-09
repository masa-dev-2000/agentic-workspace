#!/usr/bin/env python3
"""Claude Code のセッション実績を、Anthropic API 課金だったらいくらかへ換算する。

サブスクの実際の請求額ではない。「同じトークンを API で流したら」の金額。
トークン数はトランスクリプトの usage をそのまま合算するだけで、推定はしない。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

# 単価は USD / 100万トークン。cache write は TTL で倍率が変わる(5分=1.25x / 1時間=2x)、
# cache read は 0.1x。出典: claude-api skill の Current Models 表。
PRICES = {
    "claude-fable-5":   {"in": 10.00, "out": 50.00},
    "claude-mythos-5":  {"in": 10.00, "out": 50.00},
    "claude-opus-5":    {"in":  5.00, "out": 25.00},
    "claude-opus-4-8":  {"in":  5.00, "out": 25.00},
    "claude-opus-4-7":  {"in":  5.00, "out": 25.00},
    "claude-opus-4-6":  {"in":  5.00, "out": 25.00},
    "claude-sonnet-5":  {"in":  3.00, "out": 15.00},
    "claude-sonnet-4-6": {"in": 3.00, "out": 15.00},
    "claude-haiku-4-5": {"in":  1.00, "out":  5.00},
}
# Sonnet 5 は 2026-08-31 まで導入価格。--intro で切り替える。
INTRO = {"claude-sonnet-5": {"in": 2.00, "out": 10.00}}

CACHE_WRITE_5M = 1.25
CACHE_WRITE_1H = 2.00
CACHE_READ = 0.10
MTOK = 1_000_000


def price_for(model: str, intro: bool) -> dict | None:
    if intro and model in INTRO:
        return INTRO[model]
    return PRICES.get(model)


def encode_cwd(path: str) -> str:
    """Claude Code のプロジェクトディレクトリ名の作り方(区切り→ハイフン)に合わせる。"""
    return os.path.abspath(path).replace("\\", "-").replace("/", "-").replace(":", "")


def transcript_dir(project: str | None) -> str:
    base = os.path.join(os.path.expanduser("~"), ".claude", "projects")
    if project:
        return os.path.join(base, project)
    # 引数なしなら、まず今いるディレクトリに対応するものを探す
    guess = os.path.join(base, encode_cwd(os.getcwd()))
    if os.path.isdir(guess):
        return guess
    dirs = [d for d in glob.glob(os.path.join(base, "*")) if os.path.isdir(d)]
    if not dirs:
        sys.exit(f"トランスクリプトが見つかりません: {base}")
    return max(dirs, key=os.path.getmtime)


def find_session(base_dir: str, session: str) -> str:
    """セッションIDは worktree ごとの別ディレクトリに落ちることがあるので横断で探す。"""
    direct = os.path.join(base_dir, f"{session}.jsonl")
    if os.path.exists(direct):
        return direct
    root = os.path.dirname(base_dir)
    hits = glob.glob(os.path.join(root, "*", f"{session}.jsonl"))
    if not hits:
        sys.exit(f"セッションが見つかりません: {session}")
    return max(hits, key=os.path.getsize)


def iter_usage(path: str, since: datetime | None):
    """1ファイル分の assistant メッセージから usage を取り出す。"""
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("type") != "assistant":
                continue
            msg = rec.get("message")
            if not isinstance(msg, dict):
                continue
            usage = msg.get("usage")
            if not isinstance(usage, dict):
                continue
            if since is not None:
                ts = rec.get("timestamp")
                if not ts:
                    continue
                try:
                    when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if when < since:
                    continue
            yield msg.get("model") or "(unknown)", usage


def collect(files, since, intro):
    agg = defaultdict(lambda: {
        "in": 0, "out": 0, "read": 0, "w5m": 0, "w1h": 0, "calls": 0,
    })
    for path in files:
        for model, u in iter_usage(path, since):
            a = agg[model]
            a["calls"] += 1
            a["in"] += u.get("input_tokens", 0) or 0
            a["out"] += u.get("output_tokens", 0) or 0
            a["read"] += u.get("cache_read_input_tokens", 0) or 0
            cc = u.get("cache_creation") or {}
            w5 = cc.get("ephemeral_5m_input_tokens")
            w1 = cc.get("ephemeral_1h_input_tokens")
            if w5 is None and w1 is None:
                # 内訳が無い古い形式。TTL 不明なので 5 分側に寄せる(安く見積もる)
                a["w5m"] += u.get("cache_creation_input_tokens", 0) or 0
            else:
                a["w5m"] += w5 or 0
                a["w1h"] += w1 or 0
    return agg


def cost_of(model: str, a: dict, intro: bool):
    p = price_for(model, intro)
    if p is None:
        return None
    return {
        "in": a["in"] * p["in"] / MTOK,
        "w5m": a["w5m"] * p["in"] * CACHE_WRITE_5M / MTOK,
        "w1h": a["w1h"] * p["in"] * CACHE_WRITE_1H / MTOK,
        "read": a["read"] * p["in"] * CACHE_READ / MTOK,
        "out": a["out"] * p["out"] / MTOK,
    }


def fmt_tok(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.0f}k"
    return str(n)


def main() -> None:
    ap = argparse.ArgumentParser(description="Claude Code の使用量を API 課金換算する")
    ap.add_argument("--project", help="~/.claude/projects 配下のディレクトリ名(省略時は最新)")
    ap.add_argument("--session", help="セッションID(jsonl のファイル名。省略時はプロジェクト全体)")
    ap.add_argument("--hours", type=float, help="直近N時間だけを集計する")
    ap.add_argument("--since", help="この日時以降だけ集計(ISO8601、例 2026-08-02T09:00:00+09:00)")
    ap.add_argument("--rate", type=float, default=155.0, help="USD→JPY(既定155)")
    ap.add_argument("--intro", action="store_true", help="Sonnet 5 の導入価格($2/$10)で計算する")
    ap.add_argument("--json", action="store_true", help="JSONで出力する")
    args = ap.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass

    d = transcript_dir(args.project)
    if args.session:
        files = [find_session(d, args.session)]
    else:
        files = sorted(glob.glob(os.path.join(d, "*.jsonl")))
    if not files:
        sys.exit(f"jsonl がありません: {d}")

    since = None
    if args.hours:
        since = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    elif args.since:
        since = datetime.fromisoformat(args.since)
        if since.tzinfo is None:
            since = since.astimezone()

    agg = collect(files, since, args.intro)
    if not agg:
        print("対象期間に API 呼び出しがありません。")
        return

    rows, total, unknown = [], 0.0, []
    for model, a in sorted(agg.items(), key=lambda kv: -kv[1]["calls"]):
        c = cost_of(model, a, args.intro)
        if c is None:
            unknown.append(model)
            continue
        s = sum(c.values())
        total += s
        rows.append((model, a, c, s))

    if args.json:
        print(json.dumps({
            "scope": os.path.basename(d),
            "files": len(files),
            "total_usd": round(total, 4),
            "total_jpy": round(total * args.rate),
            "unpriced_models": unknown,
            "models": [
                {"model": m, "calls": a["calls"],
                 "tokens": {k: a[k] for k in ("in", "out", "read", "w5m", "w1h")},
                 "usd": {k: round(v, 4) for k, v in c.items()},
                 "usd_total": round(s, 4)}
                for m, a, c, s in rows
            ],
        }, ensure_ascii=False, indent=2))
        return

    span = "全期間" if since is None else f"{since.astimezone():%Y-%m-%d %H:%M} 以降"
    scope = args.session or f"{os.path.basename(d)} 全 {len(files)} セッション"
    print(f"対象: {scope} / {span}")
    print()
    print(f"{'model':<18}{'calls':>7}{'in':>9}{'out':>9}{'cache r':>9}{'cache w':>9}{'USD':>10}")
    print("-" * 71)
    for m, a, c, s in rows:
        print(f"{m:<18}{a['calls']:>7}{fmt_tok(a['in']):>9}{fmt_tok(a['out']):>9}"
              f"{fmt_tok(a['read']):>9}{fmt_tok(a['w5m']+a['w1h']):>9}{s:>10.2f}")
    print("-" * 71)
    print(f"{'合計':<18}{'':>7}{'':>9}{'':>9}{'':>9}{'':>9}{total:>10.2f}  "
          f"(≒ {total * args.rate:,.0f}円 @ {args.rate:.0f}円/$)")

    sub = sum(a["sub"] for _, a, _, _ in rows)
    if sub:
        print(f"\nうちサブエージェント呼び出し: {sub} 件 / 全 {sum(a['calls'] for _, a, _, _ in rows)} 件")
    if unknown:
        print(f"\n単価未登録のため除外: {', '.join(unknown)}")
    print("\n※ サブスクの実請求額ではなく「同じトークンをAPIで流した場合」の換算値。")


if __name__ == "__main__":
    main()
