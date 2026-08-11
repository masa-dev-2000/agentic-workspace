#!/usr/bin/env python3
"""Read back the research provenance log written by hooks/claude/log-reads.sh.

The log records what tools READ (path/pattern/query/url), not what was
relied upon - there is no "this result was used" event, so this is not a
citation trail. Coverage for WebFetch/WebSearch results is limited to what
tool_input carries (query/url) - the hook does not attempt to record result
URLs from a WebSearch response because that schema was not reliably
observed at runtime (see log-reads.py's header comment).

Run: python -X utf8 scripts/research_log.py [--session ID] [--today] [--summary]
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

LOG_DIR = Path.home() / ".claude" / "research-log"


def load_lines(today_only: bool) -> list[dict]:
    if not LOG_DIR.is_dir():
        return []
    if today_only:
        files = [LOG_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"]
    else:
        files = sorted(LOG_DIR.glob("*.jsonl"))
    entries: list[dict] = []
    for f in files:
        if not f.is_file():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def print_summary(entries: list[dict]) -> None:
    by_tool = Counter(e.get("tool", "?") for e in entries)
    by_agent = Counter(
        e.get("agent_type", "main-thread") for e in entries
    )
    print(f"total reads: {len(entries)}")
    print("\nby tool:")
    for tool, n in by_tool.most_common():
        print(f"  {tool}: {n}")
    print("\nby agent:")
    for agent, n in by_agent.most_common():
        print(f"  {agent}: {n}")


def print_entries(entries: list[dict]) -> None:
    for e in entries:
        print(json.dumps(e, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", help="filter to one session id")
    parser.add_argument("--today", action="store_true", help="only today's log file")
    parser.add_argument("--summary", action="store_true", help="print counts by tool/agent instead of raw lines")
    args = parser.parse_args()

    entries = load_lines(args.today)
    if args.session:
        entries = [e for e in entries if e.get("session_id") == args.session]

    if args.summary:
        print_summary(entries)
    else:
        print_entries(entries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
