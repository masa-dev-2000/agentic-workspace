#!/usr/bin/env python3
"""Helper invoked by log-reads.sh: reads the PostToolUse payload from stdin,
extracts the identifying field(s) for research-type tools, and appends one
JSONL line to %USERPROFILE%\\.claude\\research-log\\YYYY-MM-DD.jsonl.

Never logs file content or response bodies - only path/pattern/query/url,
truncated at 500 chars. Never prints anything (hook contract: silent on
success). Any error is swallowed so this hook can never break the tool call
it is observing - it is best-effort provenance, not a control.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

MAX_LEN = 500
TRUNC_MARKER = "...[truncated]"

# Only these tools represent "research reads". Other tools (Edit, Bash, ...)
# are out of scope for this log and produce no line.
IDENTIFYING_FIELDS = {
    "Read": ["file_path"],
    "Grep": ["pattern", "path", "glob"],
    "Glob": ["pattern", "path"],
    "WebSearch": ["query"],
    "WebFetch": ["url"],
}


def truncate(v: str) -> str:
    if len(v) > MAX_LEN:
        return v[:MAX_LEN] + TRUNC_MARKER
    return v


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return

    tool_name = data.get("tool_name")
    fields = IDENTIFYING_FIELDS.get(tool_name)
    if not fields:
        return  # not a research-read tool; nothing to log

    tool_input = data.get("tool_input") or {}
    identifying = {}
    for f in fields:
        v = tool_input.get(f)
        if v:
            identifying[f] = truncate(str(v))
    if not identifying:
        return  # nothing identifying was present; skip rather than log an empty line

    now = datetime.now(timezone.utc)
    entry = {
        "timestamp": now.isoformat(),
        "tool": tool_name,
        **identifying,
        "session_id": data.get("session_id"),
    }
    # Only present for subagent-originated calls; absent for main-thread calls.
    if data.get("agent_type"):
        entry["agent_type"] = data["agent_type"]
    if data.get("agent_id"):
        entry["agent_id"] = data["agent_id"]

    try:
        log_dir = Path.home() / ".claude" / "research-log"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"{now.strftime('%Y-%m-%d')}.jsonl"
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        return


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
