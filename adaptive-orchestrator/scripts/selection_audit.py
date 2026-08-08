#!/usr/bin/env python3
"""Body-free, conservative Skill selection audit for shadow-mode optimization."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import stage_runner as runner

HEX64 = re.compile(r"^[0-9a-f]{64}$")
SKILL_KEY = re.compile(r"^[a-z0-9][a-z0-9._:@+-]{0,159}$")
SOURCES = {"registry_profile", "planner_candidate", "unfiltered_baseline"}


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def text(value: object, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value or len(value) > limit or "\n" in value or "\r" in value:
        raise ValueError(f"invalid {name}")
    return value


def key(value: object) -> str:
    if not isinstance(value, str) or not SKILL_KEY.fullmatch(value):
        raise ValueError("invalid skill key")
    return value


def integer(value: object, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"invalid {name}")
    return value


def normalize(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("audit payload must be an object")
    state = payload.get("observation_state")
    health = payload.get("telemetry_health")
    if state not in {"complete", "incomplete", "failed"} or health not in {"complete", "degraded", "failed"}:
        raise ValueError("invalid observation state")
    if not isinstance(payload.get("observation_window_closed"), bool):
        raise ValueError("invalid observation_window_closed")
    candidates = {}
    for item in payload.get("candidates") or []:
        if not isinstance(item, dict):
            raise ValueError("invalid candidate")
        skill = key(item.get("skill_key"))
        sources = item.get("sources") or []
        if not isinstance(sources, list) or not sources or any(source not in SOURCES for source in sources):
            raise ValueError("invalid candidate sources")
        candidates[skill] = sorted(set(sources))
    observed = sorted({key(item) for item in payload.get("observed") or []})
    comparisons = {}
    for skill, item in (payload.get("comparisons") or {}).items():
        key(skill)
        if not isinstance(item, dict):
            raise ValueError("invalid comparison")
        metric_source = item.get("metric_source", "unavailable")
        if metric_source not in {"per_turn_runtime", "per_turn_provider", "unavailable"}:
            raise ValueError("invalid metric_source")
        comparisons[skill] = {
            "eligible_runs": integer(item.get("eligible_runs", 0), "eligible_runs", 0, 1000000),
            "eligible_sessions": integer(item.get("eligible_sessions", 0), "eligible_sessions", 0, 1000000),
            "success_rate_bp": integer(item.get("success_rate_bp", 0), "success_rate_bp", 0, 10000),
            "baseline_success_rate_bp": integer(item.get("baseline_success_rate_bp", 0), "baseline_success_rate_bp", 0, 10000),
            "noninferiority_margin_bp": integer(item.get("noninferiority_margin_bp", 500), "noninferiority_margin_bp", 0, 10000),
            "duration_reduction_bp": integer(item.get("duration_reduction_bp", 0), "duration_reduction_bp", -10000, 10000),
            "token_reduction_bp": integer(item.get("token_reduction_bp", 0), "token_reduction_bp", -10000, 10000),
            "metric_source": metric_source,
            "comparison_quality": item.get("comparison_quality", "unknown"),
            "uncertainty": item.get("uncertainty", "unknown"),
            "retry_policy": item.get("retry_policy", "unknown"),
            "wait_policy": item.get("wait_policy", "unknown"),
        }
        if any(comparisons[skill][field] not in allowed for field, allowed in {
            "comparison_quality": {"eligible", "ineligible", "unknown"},
            "uncertainty": {"pass", "fail", "unknown"},
            "retry_policy": {"excluded", "included", "unknown"},
            "wait_policy": {"excluded", "included", "unknown"},
        }.items()):
            raise ValueError("invalid comparison quality")
    return {
        "job_id": text(payload.get("job_id"), "job_id", 128),
        "session_hash": text(payload.get("session_hash"), "session_hash", 64),
        "turn_hash": text(payload.get("turn_hash"), "turn_hash", 64),
        "registry_revision": text(payload.get("registry_revision"), "registry_revision", 128),
        "taxonomy_version": text(payload.get("taxonomy_version"), "taxonomy_version", 64),
        "observation_state": state,
        "observation_window_closed": payload["observation_window_closed"],
        "telemetry_health": health,
        "candidates": candidates,
        "observed": observed,
        "comparisons": comparisons,
    }


def classify(data: dict, skill: str, sources: list[str]) -> tuple[str, str]:
    if skill in data["observed"]:
        return "selected", "observed-execution"
    if data["observation_state"] != "complete" or not data["observation_window_closed"] or data["telemetry_health"] != "complete":
        return "not_observable", "observation-incomplete"
    if "registry_profile" not in sources:
        return "candidate_signal", "planner-or-baseline-only"
    comparison = data["comparisons"].get(skill)
    if not comparison:
        return "not_comparable", "comparison-missing"
    eligible = (
        comparison["eligible_runs"] >= 5
        and comparison["eligible_sessions"] >= 3
        and comparison["comparison_quality"] == "eligible"
        and comparison["uncertainty"] == "pass"
        and comparison["retry_policy"] == "excluded"
        and comparison["wait_policy"] == "excluded"
        and comparison["success_rate_bp"] >= comparison["baseline_success_rate_bp"] - comparison["noninferiority_margin_bp"]
        and max(comparison["duration_reduction_bp"], comparison["token_reduction_bp"]) >= 2000
        and comparison["metric_source"] in {"per_turn_runtime", "per_turn_provider"}
    )
    if eligible:
        return "missed_candidate", "matched-cohort-superiority"
    if comparison["eligible_runs"] or comparison["eligible_sessions"]:
        return "candidate_signal", "evidence-below-threshold"
    return "not_comparable", "comparison-insufficient"


def audit(payload: dict, db_path: str | None = None) -> dict:
    data = normalize(payload)
    audit_id = "audit-" + digest(data)[:32]
    rows = []
    for skill, sources in sorted(data["candidates"].items()):
        classification, reason = classify(data, skill, sources)
        rows.append({"skill_key": skill, "sources": sources, "classification": classification, "reason_code": reason})
    result = {"schema": "selection_audit_v1", "audit_id": audit_id, "body_free": True, "candidates": rows}
    if db_path:
        conn = runner.connect(Path(db_path))
        try:
            runner.migrate(conn)
            with runner.Tx(conn):
                if not conn.execute("SELECT 1 FROM ao_jobs WHERE job_id=?", (data["job_id"],)).fetchone():
                    raise ValueError("job not found")
                conn.execute(
                    "INSERT OR IGNORE INTO ao_selection_audits(audit_id,job_id,session_hash,turn_hash,registry_revision,taxonomy_version,observation_state,observation_window_closed,telemetry_health,candidate_digest,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (audit_id, data["job_id"], data["session_hash"], data["turn_hash"], data["registry_revision"], data["taxonomy_version"], data["observation_state"], int(data["observation_window_closed"]), data["telemetry_health"], digest(data["candidates"]), runner.now(conn)),
                )
                for row in rows:
                        conn.execute("INSERT OR IGNORE INTO ao_selection_candidates(audit_id,skill_key,source_json,classification,reason_code) VALUES(?,?,?,?,?)", (audit_id, row["skill_key"], canonical(row["sources"]), row["classification"], row["reason_code"]))
                result["persisted"] = True
        finally:
            conn.close()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("payload")
    parser.add_argument("--db", default="")
    args = parser.parse_args()
    try:
        raw = Path(args.payload[1:]).read_text(encoding="utf-8") if args.payload.startswith("@") else args.payload
        print(json.dumps(audit(json.loads(raw), args.db or None), ensure_ascii=True, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError, runner.RunnerError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
