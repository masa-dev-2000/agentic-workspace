#!/usr/bin/env python3
"""Require a runner-backed status line before an orchestrated turn can stop."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

DB = Path(r"C:\Users\masa\.codex\skills\adaptive-orchestrator\scripts\orchestration.sqlite3")
STATUS_PREFIXES = ("[SKILL ACTIVE · ", "[SKILLS ACTIVE · ")


def digest(value: object) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def main() -> int:
    try:
        event = json.loads(sys.stdin.read().lstrip("\ufeff"))
        if not isinstance(event, dict) or event.get("stop_hook_active"):
            return 0
        message = event.get("last_assistant_message")
        if not isinstance(message, str) or not message.strip():
            return 0
        session_id = event.get("session_id")
        turn_id = event.get("turn_id")
        if not session_id or not turn_id or not DB.is_file():
            return 0
        with sqlite3.connect(DB) as conn:
            row = conn.execute(
                """
                SELECT s.status
                FROM ao_jobs j
                JOIN ao_stages s ON s.job_id=j.job_id AND s.name=j.state
                WHERE j.session_hash=? AND j.turn_hash=?
                ORDER BY j.updated_at DESC LIMIT 1
                """,
                (digest(session_id), digest(turn_id)),
            ).fetchone()
        if not row or row[0] != "running":
            return 0
        if message.lstrip().startswith(STATUS_PREFIXES):
            return 0
        print(json.dumps({
            "decision": "block",
            "reason": "このrunner実行ターンの回答先頭に、永続runner状態に基づく [SKILL ACTIVE · canonical-skill-name · phase] を付けてから完了してください。stop_hook_active=true の再試行では再度ブロックしません。",
        }, ensure_ascii=False))
    except (OSError, sqlite3.Error, TypeError, ValueError, json.JSONDecodeError):
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
