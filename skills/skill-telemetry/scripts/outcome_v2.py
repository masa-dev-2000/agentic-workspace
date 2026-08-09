from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from telemetry_store import (
    COMPLETION_EVIDENCE_CLASSES,
    EVIDENCE_CLASSES,
    TRUSTED_PROVENANCE,
    TelemetryStore,
)

RUBRIC = "outcome-v2"
META_START = "outcome_v2_cycle_start"
META_PRE_RELEASE_START = "outcome_v2_pre_release_start"
TARGETS = (
    "failure-loop-guard",
    "skill-telemetry",
    "ai-project-manager:project-orchestrator",
)
PASS_RESULTS = {"passed"}
FAIL_RESULTS = {"failed"}
THRESHOLDS = {
    "runs_per_skill": 10,
    "connection_rate": 0.95,
    "applicable_evidence_rate": 0.70,
    "max_unverified_overall": 0.40,
    "max_unverified_per_skill": 0.60,
    "manual_false_successes": 0,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical_utc(value: str) -> str:
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("cycle timestamp must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("cycle timestamp must be canonical UTC") from error
    if parsed.tzinfo is None:
        raise ValueError("cycle timestamp must be canonical UTC")
    canonical = (
        parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    )
    if canonical != value:
        raise ValueError("cycle timestamp must be canonical UTC")
    return value


def registry_contracts(path: Path) -> dict[str, set[str]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or not isinstance(data.get("skills"), list):
        raise ValueError("invalid canonical Skill registry")
    by_key = {
        item.get("key"): item
        for item in data["skills"]
        if isinstance(item, dict) and isinstance(item.get("key"), str)
    }
    contracts: dict[str, set[str]] = {}
    for key in TARGETS:
        item = by_key.get(key)
        completion = item.get("completion") if isinstance(item, dict) else None
        accepted = (
            completion.get("acceptedEvidence")
            if isinstance(completion, dict)
            else None
        )
        if not isinstance(accepted, list) or not accepted:
            raise ValueError(f"{key} completion.acceptedEvidence is required")
        if any(
            not isinstance(value, str) or value not in EVIDENCE_CLASSES
            for value in accepted
        ):
            raise ValueError(f"{key} has invalid accepted evidence class")
        classes = set(accepted)
        if "authority" not in classes:
            raise ValueError(f"{key} must accept authority evidence")
        contracts[key] = classes
    return contracts


def table_columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


def cycle_start(db: sqlite3.Connection) -> str | None:
    row = db.execute("SELECT value FROM meta WHERE key=?", (META_START,)).fetchone()
    return row[0] if row else None


def start_cycle(db: sqlite3.Connection, at: str | None = None) -> str:
    started = canonical_utc(at) if at is not None else utc_now()
    db.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO NOTHING",
        (META_START, started),
    )
    return cycle_start(db) or started


def finalize_cycle_start(db: sqlite3.Connection, at: str | None = None) -> str:
    evaluation_count = db.execute(
        "SELECT COUNT(*) FROM skill_evaluations WHERE rubric_version=?",
        (RUBRIC,),
    ).fetchone()[0]
    if evaluation_count:
        raise RuntimeError(
            f"cannot finalize cycle start after {evaluation_count} {RUBRIC} evaluations"
        )
    previous = cycle_start(db)
    started = canonical_utc(at) if at is not None else utc_now()
    if previous:
        db.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO NOTHING",
            (META_PRE_RELEASE_START, previous),
        )
    db.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (META_START, started),
    )
    return started


def evidence_for_run(db: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    evidence_cols = table_columns(db, "skill_evidence")
    link_cols = table_columns(db, "skill_run_evidence")
    required = {"evidence_id", "evidence_class", "result"}
    if not required <= evidence_cols or not {"run_id", "evidence_id"} <= link_cols:
        return []
    optional = [
        name for name in (
            "subject_hash", "subject_ref", "observed_at", "detection"
        )
        if name in evidence_cols
    ]
    selected = ["e.evidence_id", "e.evidence_class", "e.result", *[f"e.{name}" for name in optional]]
    trust_filter = (
        " AND e.provenance_trust='trusted'"
        if "provenance_trust" in evidence_cols
        else " AND 1=0"
    )
    rows = db.execute(
        f"SELECT {','.join(selected)} FROM skill_run_evidence l "
        "JOIN skill_evidence e ON e.evidence_id=l.evidence_id WHERE l.run_id=? "
        f"{trust_filter} "
        "ORDER BY e.evidence_id",
        (run_id,),
    ).fetchall()
    names = ["evidence_id", "evidence_class", "result", *optional]
    return [dict(zip(names, row)) for row in rows]


def classify(
    status: str,
    tool_failures: int,
    evidence: list[dict[str, Any]],
    accepted: set[str],
) -> tuple[str, dict[str, int] | None, list[str], list[str]]:
    applicable = [
        item for item in evidence
        if item["evidence_class"] in accepted
        or item["evidence_class"] == "domain-verdict"
    ]
    classes = sorted({item["evidence_class"] for item in applicable})
    refs = [f"evidence:{item['evidence_id']}" for item in applicable]
    if not applicable:
        return "unverified", None, classes, refs
    has_fail = any(item["result"] in FAIL_RESULTS for item in applicable)
    authority_failed = any(
        item["evidence_class"] == "authority" and item["result"] in FAIL_RESULTS
        for item in applicable
    )
    authority_passed = any(
        item["evidence_class"] == "authority" and item["result"] == "passed"
        for item in applicable
    )
    authority_score = 2 if authority_passed else 0 if authority_failed else 1
    domain_passed = any(
        item["evidence_class"] == "domain-verdict"
        and item["result"] == "passed"
        and item.get("detection") == "explicit-manual"
        for item in applicable
    )
    completion_pass = any(
        item["evidence_class"] in COMPLETION_EVIDENCE_CLASSES
        and item["evidence_class"] in accepted
        and item["result"] == "passed"
        for item in applicable
    )
    if authority_failed:
        scores = {
            "outcome_achieved": 0,
            "completion_evidence": int(completion_pass),
            "authority_safety": 0, "avoidable_rework": 0,
            "efficient_recoverable": 1 if status != "failed" else 0,
        }
        return "rejected", scores, classes, refs
    if has_fail or status in {"failed", "interrupted"}:
        scores = {
            "outcome_achieved": 0 if status == "failed" else 1,
            "completion_evidence": int(completion_pass),
            "authority_safety": authority_score,
            "avoidable_rework": 0, "efficient_recoverable": 1,
        }
        return "rework-required", scores, classes, refs
    if completion_pass and domain_passed and authority_passed:
        scores = {
            "outcome_achieved": 2,
            "completion_evidence": 2,
            "authority_safety": 2,
            "avoidable_rework": 1,
            "efficient_recoverable": 1,
        }
        return "verified-success", scores, classes, refs
    if completion_pass:
        scores = {
            "outcome_achieved": 1, "completion_evidence": 2,
            "authority_safety": authority_score, "avoidable_rework": 1,
            "efficient_recoverable": 1,
        }
        return "partial", scores, classes, refs
    return "unverified", None, classes, refs


def candidate_runs(db: sqlite3.Connection, started: str) -> list[sqlite3.Row]:
    db.row_factory = sqlite3.Row
    selected: list[sqlite3.Row] = []
    for skill in TARGETS:
        recorded = db.execute(
            """SELECT COUNT(*) FROM skill_evaluations e
               JOIN skill_runs r ON r.run_id=e.run_id
               WHERE r.skill_key=? AND r.started_at>=? AND e.rubric_version=?
                 AND r.provenance_trust=?""",
            (skill, started, RUBRIC, TRUSTED_PROVENANCE),
        ).fetchone()[0]
        remaining = max(0, THRESHOLDS["runs_per_skill"] - recorded)
        if not remaining:
            continue
        selected.extend(db.execute(
            """SELECT r.* FROM skill_runs r
               WHERE r.skill_key=? AND r.started_at>=? AND r.status<>'running'
                 AND r.provenance_trust=?
                 AND NOT EXISTS (
                   SELECT 1 FROM skill_evaluations e
                   WHERE e.run_id=r.run_id AND e.rubric_version=?
                 )
               ORDER BY r.started_at,r.run_id LIMIT ?""",
            (skill, started, TRUSTED_PROVENANCE, RUBRIC, remaining),
        ).fetchall())
    return selected


def metrics(db: sqlite3.Connection, started: str, contracts: dict[str, set[str]]) -> dict[str, Any]:
    db.row_factory = sqlite3.Row
    result: dict[str, Any] = {"rubric": RUBRIC, "cycle_started_at": started, "skills": {}}
    totals = {"runs": 0, "linked": 0, "applicable": 0, "evaluated": 0, "unverified": 0}
    for skill in TARGETS:
        runs = db.execute(
            """SELECT run_id FROM skill_runs
               WHERE skill_key=? AND started_at>=? AND status<>'running'
                 AND provenance_trust=?""",
            (skill, started, TRUSTED_PROVENANCE),
        ).fetchall()
        linked = applicable = 0
        for run in runs:
            evidence = evidence_for_run(db, run["run_id"])
            linked += bool(evidence)
            applicable += any(item["evidence_class"] in contracts[skill] for item in evidence)
        evaluations = db.execute(
            """SELECT outcome,COUNT(*) count FROM skill_evaluations e
               JOIN skill_runs r ON r.run_id=e.run_id
               WHERE r.skill_key=? AND r.started_at>=? AND e.rubric_version=?
                 AND r.provenance_trust=?
               GROUP BY outcome""",
            (skill, started, RUBRIC, TRUSTED_PROVENANCE),
        ).fetchall()
        outcomes = {row["outcome"]: row["count"] for row in evaluations}
        evaluated = sum(outcomes.values())
        item = {
            "runs": len(runs), "linked": linked, "applicable": applicable,
            "evaluated": evaluated, "outcomes": outcomes,
            "unverified_rate": outcomes.get("unverified", 0) / evaluated if evaluated else None,
        }
        result["skills"][skill] = item
        totals["runs"] += len(runs)
        totals["linked"] += linked
        totals["applicable"] += applicable
        totals["evaluated"] += evaluated
        totals["unverified"] += outcomes.get("unverified", 0)
    totals["connection_rate"] = totals["linked"] / totals["runs"] if totals["runs"] else 0.0
    totals["applicable_evidence_rate"] = totals["applicable"] / totals["runs"] if totals["runs"] else 0.0
    totals["unverified_rate"] = totals["unverified"] / totals["evaluated"] if totals["evaluated"] else None
    result["totals"] = totals
    result["thresholds"] = THRESHOLDS
    result["ready_for_manual_audit"] = (
        all(item["evaluated"] >= THRESHOLDS["runs_per_skill"] for item in result["skills"].values())
        and totals["connection_rate"] >= THRESHOLDS["connection_rate"]
        and totals["applicable_evidence_rate"] >= THRESHOLDS["applicable_evidence_rate"]
        and totals["unverified_rate"] is not None
        and totals["unverified_rate"] <= THRESHOLDS["max_unverified_overall"]
        and all(
            item["unverified_rate"] is not None
            and item["unverified_rate"] <= THRESHOLDS["max_unverified_per_skill"]
            for item in result["skills"].values()
        )
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Prospective structured-evidence outcome-v2 evaluator")
    parser.add_argument(
        "command", choices=("start", "finalize-start", "evaluate", "status")
    )
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--at", help="ISO-8601 cycle start; intended for deterministic tests only")
    parser.add_argument("--registry", type=Path, default=Path(__file__).resolve().parents[2] / "skill-registry.yaml")
    args = parser.parse_args()
    write_mode = args.command in {"start", "finalize-start"} or (
        args.command == "evaluate" and args.record
    )
    store = (
        TelemetryStore(drain=False)
        if write_mode
        else TelemetryStore(initialize=False)
    )
    contracts = registry_contracts(args.registry)
    pending_records: list[tuple[Any, ...]] = []
    connection = store.connection if write_mode else store.read_connection
    with connection() as db:
        started = cycle_start(db)
        if args.command == "start":
            started = start_cycle(db, args.at)
        elif args.command == "finalize-start":
            started = finalize_cycle_start(db, args.at)
        if not started:
            print(json.dumps({"state": "not-started", "rubric": RUBRIC}, indent=2))
            return 2
        if args.command == "evaluate":
            for run in candidate_runs(db, started):
                evidence = evidence_for_run(db, run["run_id"])
                outcome, scores, classes, refs = classify(
                    run["status"], run["tool_failure_count"], evidence,
                    contracts[run["skill_key"]],
                )
                if args.record:
                    pending_records.append((run["run_id"], outcome, scores, classes, refs))
    for run_id, outcome, scores, classes, refs in pending_records:
        store.add_evaluation(
            run_id, outcome, scores, classes, refs,
            "codex-structured-evidence", rubric_version=RUBRIC,
        )
    with connection() as db:
        print(json.dumps(metrics(db, started, contracts), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
