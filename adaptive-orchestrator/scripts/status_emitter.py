#!/usr/bin/env python3
"""Emit a Skill status justified by persisted runner state."""
from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path

PHASES = {"planning": "plan", "reviewing": "review", "implementing": "implement", "verifying": "verify", "reporting": "report"}

def status_line(db: Path, job_id: str, skills: list[str]) -> str:
    skills = [s.strip() for s in skills if s.strip()]
    if not skills:
        raise ValueError("at least one canonical skill name is required")
    not_connected = f"[SKILL NOT_CONNECTED · {skills[0]}]"
    pending = f"[SKILL PENDING · {skills[0]}]"
    if not db.is_file():
        return not_connected
    try:
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            job = conn.execute("SELECT state FROM ao_jobs WHERE job_id=?", (job_id,)).fetchone()
            if not job:
                return not_connected
            state = job["state"]
    except sqlite3.Error:
        return not_connected
    if state == "blocked":
        return f"[SKILL BLOCKED · {skills[0]}]"
    if state in {"waiting_approval", "approval_required"}:
        return f"[SKILL WAITING_APPROVAL · {skills[0]}]"
    if state in {"completed", "cancelled"}:
        return f"[SKILL COMPLETED · {skills[0]}]"
    if state not in PHASES:
        return not_connected
    try:
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            stage = conn.execute("SELECT status, lease_until FROM ao_stages WHERE job_id=? AND name=?", (job_id, state)).fetchone()
    except sqlite3.Error:
        return not_connected
    if not stage:
        return not_connected
    if stage["status"] != "running":
        return pending
    if not stage["lease_until"] or stage["lease_until"] < int(time.time()):
        return pending
    prefix = "SKILLS" if len(skills) > 1 else "SKILL"
    return f"[{prefix} ACTIVE · {' → '.join(skills)} · {PHASES[state]}]"

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    parser.add_argument("skills", nargs="+")
    parser.add_argument("--db", default=str(Path(__file__).with_name("orchestration.sqlite3")))
    args = parser.parse_args()
    print(status_line(Path(args.db), args.job_id, args.skills))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
