"""Read-only correlation observer for Hook -> Runner -> Agent evidence."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


def observe(db_path: str | Path, job_id: str, ingress_id: str | None = None) -> dict[str, Any]:
    path = Path(db_path)
    evidence: dict[str, Any] = {"schema": "runtime-observation-v1", "job_id": job_id, "ingress_id": ingress_id or "unavailable", "observations": {}}
    if not path.is_file():
        evidence["status"] = "NOT_OBSERVABLE"
        evidence["reason"] = "database_not_found"
        return evidence
    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            ingress = None
            if ingress_id:
                row = conn.execute("SELECT ingress_id,job_id,hook_revision,created_at FROM ao_hook_ingress WHERE ingress_id=?", (ingress_id,)).fetchone()
                ingress = dict(row) if row else None
            job_row = conn.execute("SELECT job_id,state,version,plan_digest,updated_at FROM ao_jobs WHERE job_id=?", (job_id,)).fetchone()
            stages = [dict(row) for row in conn.execute("SELECT name,status,current_attempt,version,result_status,evidence_digest FROM ao_stages WHERE job_id=? ORDER BY rowid", (job_id,))]
            dispatches = [dict(row) for row in conn.execute("SELECT dispatch_id,stage,attempt,active,terminal_status,model_class,provider FROM ao_dispatches WHERE job_id=? ORDER BY created_at", (job_id,))]
            events = [dict(row) for row in conn.execute("SELECT event_type,stage,created_at FROM ao_events WHERE job_id=? ORDER BY id", (job_id,))]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        evidence["status"] = "NOT_OBSERVABLE"
        evidence["reason"] = "database_read_failed"
        evidence["error_class"] = type(exc).__name__
        return evidence
    evidence["observations"]["HOOK_RECEIVED"] = {"status": "OBSERVED" if ingress else "NOT_OBSERVABLE", "evidence": {"hook_revision": ingress.get("hook_revision") if ingress else "unavailable", "ingress_id": ingress_id or "unavailable"}}
    evidence["observations"]["RUNNER_JOB"] = {"status": "OBSERVED" if job_row else "NOT_OBSERVABLE", "evidence": {"state": job_row["state"] if job_row else "unavailable", "version": job_row["version"] if job_row else "unavailable"}}
    evidence["observations"]["RUNNER_STAGE"] = {"status": "OBSERVED" if stages else "NOT_OBSERVABLE", "evidence": {"stage_count": len(stages), "statuses": {row["name"]: row["status"] for row in stages}}}
    evidence["observations"]["SUBAGENT_DISPATCH"] = {"status": "OBSERVED" if dispatches else "NOT_OBSERVABLE", "evidence": {"dispatch_count": len(dispatches), "terminal_count": sum(row["terminal_status"] is not None for row in dispatches)}}
    root_event = next((event for event in events if event["event_type"] == "root-integrated"), None)
    evidence["observations"]["ROOT_INTEGRATED"] = {"status": "OBSERVED" if root_event else "NOT_OBSERVABLE", "evidence": {"event_type": "root-integrated" if root_event else "unavailable"}}
    states = [item["status"] for item in evidence["observations"].values()]
    evidence["status"] = "OBSERVED" if states and all(state == "OBSERVED" for state in states) else "PARTIAL"
    evidence["limitations"] = ["Codex internal context acceptance and model selection are not observable from local SQLite"]
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description="Observe orchestration evidence without writing state")
    parser.add_argument("job_id")
    parser.add_argument("--ingress-id")
    parser.add_argument("--db", default=str(Path.home() / ".codex" / "adaptive-orchestrator" / "orchestration.sqlite3"))
    args = parser.parse_args()
    print(json.dumps(observe(args.db, args.job_id, args.ingress_id), ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
