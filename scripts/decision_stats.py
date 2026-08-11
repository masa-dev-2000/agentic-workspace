#!/usr/bin/env python3
"""Measure how well automated decisions agree with human review.

Decision blocks are recorded as comments on the issue they concern (criterion
`decision-risk-levels`), so the issue stays the single place holding both the work
and the judgement about it. This script pulls them back out and computes, per
decision class, the agreement rate that determines how much autonomy that class
has earned.

Run: python -X utf8 scripts/decision_stats.py            # summary
     python -X utf8 scripts/decision_stats.py --pending  # unreviewed only
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import defaultdict

REPO = "masa-dev-2000/agentic-workspace"
PROMOTE_AT, DEMOTE_BELOW, PROMOTE_SAMPLE = 0.90, 0.80, 20

BLOCK_RE = re.compile(r"<!-- decision -->\s*(.*?)(?:\n\s*\n|\Z)", re.S)
FIELD_RE = re.compile(r"^\s*([a-z-]+):\s*(.+?)\s*$", re.M)


def fetch_comments() -> list[tuple[int, str]]:
    proc = subprocess.run(
        ["gh", "issue", "list", "--repo", REPO, "--state", "all", "--limit", "200",
         "--json", "number,comments"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        print(f"ERROR: gh failed: {proc.stderr.strip()[:200]}")
        raise SystemExit(2)
    out = []
    for issue in json.loads(proc.stdout or "[]"):
        for c in issue.get("comments") or []:
            out.append((issue["number"], c.get("body") or ""))
    return out


def parse_decisions() -> list[dict]:
    decisions = []
    for number, body in fetch_comments():
        for block in BLOCK_RE.findall(body):
            d = dict(FIELD_RE.findall(block))
            if "class" in d:
                d["issue"] = number
                decisions.append(d)
    return decisions


def main() -> int:
    decisions = parse_decisions()
    if not decisions:
        print("No decision records yet. They appear once issue-ledger runs with the\n"
              "decision-recording contract (criterion: decision-risk-levels).")
        return 0

    pending = [d for d in decisions if d.get("review", "pending") == "pending"]
    if "--pending" in sys.argv:
        print(f"UNREVIEWED DECISIONS ({len(pending)})")
        # Low confidence first: reviewing those is what keeps review affordable.
        order = {"low": 0, "medium": 1, "high": 2}
        for d in sorted(pending, key=lambda x: order.get(x.get("confidence", "medium"), 1)):
            print(f"  #{d['issue']:<4} {d['class']:<20} {d.get('risk','?'):<3} "
                  f"conf={d.get('confidence','?'):<6} basis={d.get('basis','?')}")
            print(f"        -> {d.get('conclusion','')[:100]}")
        return 0

    by_class: dict[str, list[dict]] = defaultdict(list)
    for d in decisions:
        by_class[d["class"]].append(d)

    print(f"{'class':<22}{'risk':<6}{'reviewed':<10}{'agree':<8}{'rate':<8}action")
    for cls, items in sorted(by_class.items()):
        reviewed = [d for d in items if d.get("review") in ("agree", "disagree")]
        agree = sum(1 for d in reviewed if d["review"] == "agree")
        rate = agree / len(reviewed) if reviewed else None
        risk = items[-1].get("risk", "?")
        if rate is None:
            action = "no review data yet"
        elif rate < DEMOTE_BELOW and risk == "L1":
            action = f"DEMOTE to L2 (below {DEMOTE_BELOW:.0%})"
        elif rate >= PROMOTE_AT and len(reviewed) >= PROMOTE_SAMPLE and risk == "L2":
            action = f"propose PROMOTE to L1 ({len(reviewed)} reviewed)"
        elif rate >= PROMOTE_AT and risk == "L2":
            action = f"on track ({len(reviewed)}/{PROMOTE_SAMPLE} toward promotion)"
        else:
            action = "hold"
        rate_s = f"{rate:.0%}" if rate is not None else "-"
        print(f"{cls:<22}{risk:<6}{len(reviewed):<10}{agree:<8}{rate_s:<8}{action}")

    print(f"\nunreviewed: {len(pending)}  (see --pending)")
    print("Promotion and demotion are themselves L3: a human executes them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
