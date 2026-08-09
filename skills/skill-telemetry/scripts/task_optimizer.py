from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
import os
import hashlib
import sqlite3
from pathlib import Path
from statistics import mean
from typing import Any
import re

try:
    import yaml
except Exception:  # pragma: no cover - validator reports unavailable dependency
    yaml = None

SCHEMA_VERSION = "candidate_v2"
MIGRATION_VERSION = "legacy-to-v2-1"


def _cutoff(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=max(1, days))).replace(
        microsecond=0
    ).isoformat()


def _registry_contracts() -> dict[str, dict[str, str]]:
    """Read the local registry's immutable contract identifiers without importing YAML."""
    path = Path(__file__).resolve().parents[2] / "skill-registry.yaml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    result: dict[str, dict[str, str]] = {}
    current: str | None = None
    for line in text.splitlines():
        match = re.match(r"\s*- key:\s*(\S+)\s*$", line)
        if match:
            current = match.group(1).strip()
            continue
        if current is None:
            continue
        match = re.match(r"\s*contractFingerprint:\s*(\S+)\s*$", line)
        if match:
            result.setdefault(current, {})["contractFingerprint"] = match.group(1)
        match = re.match(r"\s*contractContentDigest:\s*(\S+)\s*$", line)
        if match:
            result.setdefault(current, {})["contractContentDigest"] = match.group(1)
    return result


def _report_digest(report: dict[str, Any]) -> str:
    body = {key: value for key, value in report.items() if key != "report_digest"}
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def validate_candidate(candidate: dict[str, Any], contracts: dict[str, dict[str, str]] | None = None) -> tuple[bool, str]:
    """Fail closed for legacy, unbound, or non-reviewable candidate metadata."""
    if candidate.get("schema_version") != SCHEMA_VERSION:
        return False, "legacy-schema"
    target = candidate.get("target")
    if not isinstance(target, dict) or not target.get("skill_key"):
        return False, "missing-target"
    contracts = contracts if contracts is not None else _registry_contracts()
    expected = contracts.get(str(target["skill_key"]), {})
    if not expected or any(target.get(key) != expected.get(key) for key in ("contractFingerprint", "contractContentDigest")):
        return False, "target-fingerprint-mismatch"
    if not candidate.get("source_report_digest") or not candidate.get("source_report_generated_at"):
        return False, "missing-source-digest"
    evidence = candidate.get("evidence")
    if not isinstance(evidence, dict) or not evidence.get("evidence_refs") or int(evidence.get("sample_size", 0)) < 20:
        return False, "missing-evidence-provenance"
    if not isinstance(candidate.get("before_metrics"), dict):
        return False, "missing-before-metrics"
    if candidate.get("before_metrics", {}).get("verified_success_task_rate") in (0, None):
        return False, "missing-verified-success"
    proposal = candidate.get("proposal")
    impact = candidate.get("impact")
    validation = candidate.get("validation_plan")
    if not isinstance(proposal, dict) or not proposal.get("concrete_scope") or not proposal.get("expected_delta"):
        return False, "missing-concrete-scope"
    if not isinstance(impact, dict) or not impact.get("rollback_ref"):
        return False, "missing-impact-rollback"
    if not isinstance(validation, dict) or not validation.get("fixture_refs") or not validation.get("eval_ids") or not validation.get("thresholds") or not validation.get("nonregression") or int(validation.get("min_samples", 0)) < 20:
        return False, "missing-validation-plan"
    if candidate.get("approval_required") is not True:
        return False, "approval-boundary-missing"
    status = candidate.get("status")
    if status not in {"candidate-awaiting-approval", "insufficient-evidence", "legacy-invalid", "approved", "applied", "verified", "rolled_back", "expired"}:
        return False, "unknown-lifecycle-status"
    if status in {"approved", "applied", "verified", "rolled_back"} and not isinstance(candidate.get("approval"), dict):
        return False, "approval-required"
    if status in {"applied", "verified", "rolled_back"} and not isinstance(candidate.get("application"), dict):
        return False, "application-required"
    return True, "ok"


def validate_approval(candidate: dict[str, Any], candidate_id: str, target: dict[str, str], approval_ref: str) -> tuple[bool, str]:
    """Require an explicit approval bound to the exact candidate and registry target."""
    if candidate.get("candidate_id") != candidate_id:
        return False, "candidate-id-mismatch"
    current = _registry_contracts().get(str(candidate.get("target", {}).get("skill_key", "")), {})
    if not current or any(target.get(key) != current.get(key) for key in ("contractFingerprint", "contractContentDigest")):
        return False, "target-fingerprint-stale"
    if candidate.get("target") != target:
        return False, "target-fingerprint-mismatch"
    if not approval_ref or not approval_ref.startswith("local:"):
        return False, "invalid-approval-ref"
    return True, "ok"


def validate_application(candidate: dict[str, Any], candidate_id: str, target: dict[str, str], approval_ref: str, changeset_ref: str, before_digest: str) -> tuple[bool, str]:
    """Guard an application without performing it; callers must supply exact approved metadata."""
    ok, reason = validate_approval(candidate, candidate_id, target, approval_ref)
    if not ok:
        return False, reason
    if candidate.get("status") != "approved":
        return False, "candidate-not-approved"
    if candidate.get("approval", {}).get("approval_ref") != approval_ref:
        return False, "approval-ref-mismatch"
    if not changeset_ref.startswith("local:"):
        return False, "invalid-changeset-ref"
    if before_digest != target.get("contractContentDigest"):
        return False, "before-digest-mismatch"
    return True, "ok"


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def approve_candidate(path: str, candidate_id: str, target: dict[str, str], approval_ref: str, actor_ref: str) -> dict[str, Any]:
    candidate_path = Path(path)
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    valid, reason = validate_candidate(candidate)
    if not valid:
        raise ValueError(f"candidate-invalid:{reason}")
    ok, reason = validate_approval(candidate, candidate_id, target, approval_ref)
    if not ok:
        raise ValueError(reason)
    if candidate.get("status") != "candidate-awaiting-approval":
        raise ValueError("invalid-transition")
    candidate.update({"status": "approved", "approval": {"approval_ref": approval_ref, "actor_ref": actor_ref, "approved_at": datetime.now(timezone.utc).isoformat()}})
    _write_json_atomic(candidate_path, candidate)
    return candidate


def apply_candidate(path: str, candidate_id: str, target: dict[str, str], approval_ref: str, changeset_ref: str, actor_ref: str) -> dict[str, Any]:
    candidate_path = Path(path)
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    before_digest = target.get("contractContentDigest", "")
    ok, reason = validate_application(candidate, candidate_id, target, approval_ref, changeset_ref, before_digest)
    if not ok:
        raise ValueError(reason)
    candidate.update({"status": "applied", "application": {"changeset_ref": changeset_ref, "before_digest": before_digest, "actor_ref": actor_ref, "applied_at": datetime.now(timezone.utc).isoformat()}, "after_metrics": None})
    _write_json_atomic(candidate_path, candidate)
    return candidate


def rollback_candidate(path: str, candidate_id: str, target: dict[str, str], rollback_ref: str, actor_ref: str) -> dict[str, Any]:
    candidate_path = Path(path)
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    if candidate.get("candidate_id") != candidate_id or candidate.get("status") not in {"applied", "verified"}:
        raise ValueError("invalid-transition")
    current = _registry_contracts().get(str(candidate.get("target", {}).get("skill_key", "")), {})
    if not current or target != {"skill_key": candidate.get("target", {}).get("skill_key", ""), **current} or candidate.get("target") != target:
        raise ValueError("target-fingerprint-stale")
    if rollback_ref != candidate.get("impact", {}).get("rollback_ref"):
        raise ValueError("rollback-ref-mismatch")
    candidate.update({"status": "rolled_back", "rollback": {"rollback_ref": rollback_ref, "actor_ref": actor_ref, "rolled_back_at": datetime.now(timezone.utc).isoformat()}})
    _write_json_atomic(candidate_path, candidate)
    return candidate


def build_report(
    store,
    days: int = 30,
    limit: int = 20,
    freshness: dict[str, Any] | None = None,
    drain_result: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Build privacy-safe optimization recommendations from trusted telemetry.

    A task is approximated by one pseudonymized session/turn pair. No hashes,
    prompts, responses, or tool payloads are returned.
    """
    rows = store.rows(
        """SELECT r.run_id,r.skill_key,r.session_hash,r.turn_hash,r.status,
                  r.started_at,r.duration_ms,r.duration_quality,
                  r.tool_failure_count,r.provenance_trust,
                  COALESCE(e.outcome,'') evaluation_outcome,
                  COALESCE(f.positive,0) positive_feedback,
                  COALESCE(f.negative,0) negative_feedback
           FROM skill_runs r
           LEFT JOIN skill_evaluations e ON e.evaluation_id=(
             SELECT e2.evaluation_id FROM skill_evaluations e2
             WHERE e2.run_id=r.run_id
             ORDER BY e2.reviewed_at DESC,e2.evaluation_id DESC LIMIT 1
           )
           LEFT JOIN (
             SELECT run_id,
                    SUM(CASE WHEN sentiment='positive' THEN 1 ELSE 0 END) positive,
                    SUM(CASE WHEN sentiment='negative' THEN 1 ELSE 0 END) negative
             FROM skill_feedback GROUP BY run_id
           ) f ON f.run_id=r.run_id
           WHERE r.started_at>=? AND r.status<>'running'
             AND r.session_hash<>'' AND r.turn_hash<>''
             AND r.provenance_trust='trusted'
           ORDER BY r.session_hash,r.turn_hash,r.started_at,r.run_id""",
        (_cutoff(days),),
    )

    tasks: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        tasks[(row["session_hash"], row["turn_hash"])].append(row)

    skill_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "invocations": 0,
            "tasks": set(),
            "returned": 0,
            "failed_or_interrupted": 0,
            "tool_failures": 0,
            "durations": [],
            "verified_success_tasks": set(),
            "negative_feedback": 0,
            "repeated_task_count": 0,
        }
    )
    patterns: dict[tuple[str, ...], dict[str, Any]] = defaultdict(
        lambda: {"tasks": 0, "verified_success_tasks": 0}
    )
    repeated_tasks = 0
    verified_tasks = 0
    returned_tasks = 0
    failed_tasks = 0

    for task_id, task_rows in tasks.items():
        ordered = sorted(task_rows, key=lambda r: (r["started_at"], r["run_id"]))
        sequence = tuple(row["skill_key"] for row in ordered)
        compressed = tuple(
            skill for index, skill in enumerate(sequence)
            if index == 0 or skill != sequence[index - 1]
        )
        verified = any(row["evaluation_outcome"] == "verified-success" for row in ordered)
        if verified:
            verified_tasks += 1
        if all(row["status"] == "returned" for row in ordered):
            returned_tasks += 1
        if any(row["status"] in {"failed", "interrupted"} for row in ordered):
            failed_tasks += 1
        if len(sequence) != len(set(sequence)):
            repeated_tasks += 1

        patterns[compressed]["tasks"] += 1
        patterns[compressed]["verified_success_tasks"] += int(verified)
        seen_skills: set[str] = set()
        for row in ordered:
            skill = row["skill_key"]
            item = skill_stats[skill]
            item["invocations"] += 1
            item["tasks"].add(task_id)
            item["returned"] += int(row["status"] == "returned")
            item["failed_or_interrupted"] += int(
                row["status"] in {"failed", "interrupted"}
            )
            item["tool_failures"] += row["tool_failure_count"] or 0
            item["negative_feedback"] += row["negative_feedback"] or 0
            if row["duration_quality"] == "exact" and row["duration_ms"] is not None:
                item["durations"].append(row["duration_ms"])
            if row["evaluation_outcome"] == "verified-success":
                item["verified_success_tasks"].add(task_id)
            if skill in seen_skills:
                item["repeated_task_count"] += 1
            seen_skills.add(skill)

    skill_report = []
    recommendations = []
    for skill, item in skill_stats.items():
        task_count = len(item["tasks"])
        failure_rate = item["failed_or_interrupted"] / item["invocations"]
        repeat_rate = item["repeated_task_count"] / item["invocations"]
        avg_duration = round(mean(item["durations"])) if item["durations"] else None
        verified_rate = (
            len(item["verified_success_tasks"]) / task_count if task_count else None
        )
        confidence = "high" if task_count >= 20 else "medium" if task_count >= 5 else "low"
        report_item = {
            "skill": skill,
            "invocations": item["invocations"],
            "tasks": task_count,
            "failure_or_interruption_rate": round(failure_rate, 3),
            "repeat_invocation_rate": round(repeat_rate, 3),
            "tool_failures": item["tool_failures"],
            "average_exact_duration_ms": avg_duration,
            "verified_success_task_rate": (
                round(verified_rate, 3) if verified_rate is not None else None
            ),
            "negative_feedback": item["negative_feedback"],
            "confidence": confidence,
        }
        skill_report.append(report_item)
        if repeat_rate >= 0.30 and task_count >= 5:
            recommendations.append({
                "type": "redundant-invocation",
                "priority": "high" if repeat_rate >= 0.50 else "medium",
                "skill": skill,
                "reason": "同一タスク内で同じSkillが繰り返し発動している",
                "metric": {"repeat_invocation_rate": round(repeat_rate, 3)},
                "action": "発動条件を狭めるか、同一タスク内の結果を再利用する",
            })
        if failure_rate >= 0.20 or item["tool_failures"] > item["invocations"]:
            recommendations.append({
                "type": "failure-review",
                "priority": "high" if failure_rate >= 0.35 else "medium",
                "skill": skill,
                "reason": "失敗・中断またはツール失敗が多い",
                "metric": {
                    "failure_or_interruption_rate": round(failure_rate, 3),
                    "tool_failures": item["tool_failures"],
                },
                "action": "失敗経路を分解し、前提確認と復旧手順を短縮する",
            })

    pattern_report = []
    for sequence, item in sorted(patterns.items(), key=lambda pair: -pair[1]["tasks"]):
        if item["tasks"] < 2:
            continue
        pattern_report.append({
            "skill_sequence": list(sequence),
            "tasks": item["tasks"],
            "verified_success_tasks": item["verified_success_tasks"],
            "verified_success_rate": round(
                item["verified_success_tasks"] / item["tasks"], 3
            ),
        })

    if tasks and verified_tasks == 0:
        recommendations.append({
            "type": "measurement-gap",
            "priority": "high",
            "reason": "対象期間にverified-successがなく、Skillの成果比較ができない",
            "action": "代表タスクにcompletion evidenceとdomain verdictを付与する",
        })

    skill_report.sort(key=lambda item: (-item["invocations"], item["skill"]))
    recommendations.sort(key=lambda item: (item["priority"] != "high", item["type"], item.get("skill", "")))
    freshness = freshness or store.freshness()
    drain_result = drain_result or {}
    pending_events = int(freshness.get("spool_pending", 0))
    incomplete = pending_events > 0 or int(drain_result.get("deferred", 0)) > 0
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": max(1, days),
        "trusted_task_count": len(tasks),
        "trusted_run_count": len(rows),
        "returned_task_count": returned_tasks,
        "failed_or_interrupted_task_count": failed_tasks,
        "verified_success_task_count": verified_tasks,
        "repeated_skill_task_count": repeated_tasks,
        "skill_metrics": skill_report[: max(1, min(limit, 100))],
        "common_skill_sequences": pattern_report[: max(1, min(limit, 100))],
        "recommendations": recommendations[: max(1, min(limit, 100))],
        "freshness": {
            "state": "stale" if incomplete else "fresh",
            "data_cutoff": freshness.get("latest_receipt_at"),
            "latest_run_started_at": freshness.get("latest_run_started_at"),
            "pending_events": pending_events,
            "drain_deferred": int(drain_result.get("deferred", 0)),
            "drain_processed": int(drain_result.get("processed", 0)),
        },
        "limitations": [
            "taskは同一session・turnのSkill実行で近似している",
            "入力・出力トークン、モデル単価、プロンプト本文は未取得",
            "verified-successは評価されたRunのSkillだけへ帰属する",
            "推奨は自動適用せず、Skill本文の変更は人間承認が必要",
        ],
    }


def write_latest_report(
    store,
    days: int = 30,
    limit: int = 100,
    freshness: dict[str, Any] | None = None,
    drain_result: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Persist only the latest body-free advisory report."""
    report = build_report(store, days, limit, freshness, drain_result)
    report["report_digest"] = _report_digest(report)
    output_dir = Path(store.root) / "optimization"
    output_dir.mkdir(parents=True, exist_ok=True)
    reports_dir = output_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    immutable = reports_dir / f"{report['generated_at'].replace(':', '').replace('+00:00', 'Z')}-{report['report_digest'].split(':', 1)[-1]}.json"
    immutable.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    target = output_dir / "latest.json"
    temporary = output_dir / f".latest.{os.getpid()}.tmp"
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)
    return report


def _registry_fingerprint(skill: str) -> dict[str, str] | None:
    root = Path(__file__).resolve().parents[2]
    path = root / "skill-registry.yaml"
    if yaml is None or not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
        for item in data.get("skills", []):
            if item.get("key") == skill:
                return {"contractFingerprint": str(item.get("contractFingerprint", "")), "contractContentDigest": str(item.get("contractContentDigest", ""))}
    except Exception:
        return None
    return None


def _write_candidate_bundles_v2(store, report: dict[str, Any]) -> list[str]:
    root = Path(store.root) / "optimization"
    candidate_root = root / "candidates"
    rejected_root = root / "rejected"
    legacy_root = root / "legacy-invalid"
    candidate_root.mkdir(parents=True, exist_ok=True)
    rejected_root.mkdir(parents=True, exist_ok=True)
    legacy_root.mkdir(parents=True, exist_ok=True)
    # Do not scan thousands of legacy files on every 15-minute run. Approval
    # validation rejects any non-v2 file; this manifest makes that quarantine
    # policy explicit without turning optimization into an expensive migration.
    (legacy_root / "manifest.json").write_text(json.dumps({
        "schema_version": SCHEMA_VERSION,
        "status": "legacy-invalid",
        "policy": "all candidate files without schema_version=candidate_v2 are ineligible for approval or application",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    digest = str(report.get("report_digest") or _report_digest(report))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    created=[]
    for item in report.get("skill_metrics", []):
        skill = str(item.get("skill", "")); fp = _registry_fingerprint(skill)
        recs=[r for r in report.get("recommendations", []) if r.get("skill")==skill]
        if item.get("tasks", 0) < 20 or not recs or not fp or not fp.get("contractFingerprint") or not fp.get("contractContentDigest") or not item.get("verified_success_task_rate"):
            rej={"schema_version":"candidate_v2","status":"insufficient-evidence","target_skill":skill,"source_report_generated_at":report.get("generated_at"),"source_report_digest":digest,"reason":"missing target fingerprint, actionable recommendation, or verified-success evidence","before_metrics":item}
            (rejected_root / f"{stamp}-{skill.replace(':','_')}.json").write_text(json.dumps(rej,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
            continue
        rid=stamp+'-'+skill.replace(':','_')
        if any(p.get("candidate_id") == rid or (p.get("target", {}).get("skill_key") == skill and p.get("source_report_digest") == digest) for path in candidate_root.glob("*.json") for p in [json.loads(path.read_text(encoding="utf-8"))]):
            continue
        primary = recs[0]
        metric = primary.get("metric", {})
        payload={
            "schema_version":"candidate_v2",
            "candidate_id":rid,
            "skill":skill,
            "source_report_generated_at":report.get("generated_at"),
            "source_report_digest":digest,
            "target":{"skill_key":skill, **fp},
            "evidence":{"provenance":"trusted-telemetry","window_days":report.get("window_days"),"sample_size":item.get("tasks"),"evidence_refs":["local:telemetry/"+digest.split(":",1)[-1]]},
            "before_metrics":item,
            "proposal":{"problem":primary.get("reason",""),"mechanism":"reduce the observed skill-specific failure or repetition path","concrete_scope":f"{skill}: add an explicit precondition check and bounded recovery branch for the observed {primary.get('type','failure')} pattern","expected_delta":{"failure_or_interruption_rate":"decrease","tool_failures":"decrease","baseline":metric}},
            "change_ref":"local:optimization-proposal/"+rid,
            "change_summary":primary.get("action",""),
            "rationale_refs":["local:optimization/"+rid],
            "impact":{"severity":primary.get("priority","medium"),"affected_scope":"skill-local","safety_risks":["regression"],"rollback_ref":"local:rollback/"+rid},
            "validation_plan":{"fixture_refs":["local:fixture/failure-recovery"],"commands":["skill-specific-tests"],"eval_ids":["before-after-outcome","safety-nonregression"],"min_samples":20,"duration":"bounded-window","thresholds":{"failure_or_interruption_rate":"< baseline","tool_failures":"<= baseline","verified_success_task_rate":"> baseline"},"nonregression":["existing-tests","authority-boundary"]},
            "status":"candidate-awaiting-approval",
            "approval_required":True,
            "approval":None,
            "application":None,
            "after_metrics":None,
            "evaluation_refs":[],
        }
        valid, reason = validate_candidate(payload)
        if not valid:
            (rejected_root / f"{rid}.json").write_text(json.dumps({"schema_version":SCHEMA_VERSION,"status":"insufficient-evidence","candidate_id":rid,"reason":reason},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
            continue
        (candidate_root/f"{rid}.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); created.append(str(candidate_root/f"{rid}.json"))
    return created


def write_candidate_bundles(store, report: dict[str, Any]) -> list[str]:
    """Compatibility wrapper: all candidate generation uses the v2 writer."""
    return _write_candidate_bundles_v2(store, report)


def migrate_legacy_candidates(root: str | Path, limit: int = 500, write: bool = False) -> dict[str, Any]:
    """Convert legacy candidate records without deleting or mutating originals."""
    raw_root = str(root)
    if raw_root.startswith("\\\\") or raw_root.startswith("//"):
        raise ValueError("migration-root-must-be-local")
    root_path = Path(root).resolve()
    source_dir = root_path / "optimization" / "candidates"
    output_dir = root_path / "optimization" / "migrated-v2"
    if write:
        output_dir.mkdir(parents=True, exist_ok=True)
    converted: list[str] = []
    measurement_gap: list[str] = []
    rejected: list[str] = []
    mappings: list[dict[str, str]] = []
    skipped: list[str] = []
    reports_dir = root_path / "optimization" / "reports"

    def trusted_evidence_exists(refs: Any) -> bool:
        if not isinstance(refs, list) or not refs:
            return False
        ids = []
        for ref in refs:
            if not isinstance(ref, str) or not ref.startswith("local:evidence/"):
                return False
            ids.append(ref.split("/", 1)[1])
        db_path = root_path / "telemetry.sqlite3"
        if not db_path.exists():
            return False
        connection = None
        try:
            connection = sqlite3.connect(db_path)
            rows = connection.execute(
                "SELECT evidence_id FROM skill_evidence WHERE evidence_id IN (%s) AND provenance_trust='trusted'" % ",".join("?" for _ in ids),
                ids,
            ).fetchall()
            return len(rows) == len(set(ids))
        except sqlite3.Error:
            return False
        finally:
            if connection is not None:
                connection.close()

    def trusted_report_exists(digest: str) -> bool:
        if not digest or not reports_dir.exists():
            return False
        for report_path in reports_dir.glob("*.json"):
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if report.get("report_digest") == digest and _report_digest(report) == digest:
                return True
        return False
    for source in sorted(source_dir.glob("*.json"))[: max(1, min(limit, 5000))]:
        try:
            raw_bytes = source.read_bytes()
            legacy = json.loads(raw_bytes.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            rejected.append(source.name)
            continue
        if legacy.get("schema_version") == SCHEMA_VERSION:
            continue
        skill = str(legacy.get("skill") or legacy.get("target_skill") or "")
        contract = _registry_fingerprint(skill) if skill else None
        metrics = legacy.get("metrics") or legacy.get("before_metrics") or {}
        source_digest = "sha256:" + hashlib.sha256(raw_bytes).hexdigest()
        source_report_digest = legacy.get("source_report_digest")
        evidence_refs = (legacy.get("evidence") or {}).get("evidence_refs") or legacy.get("evidence_refs")
        proposal = legacy.get("proposal") or {}
        validation = legacy.get("validation_plan") or {}
        candidate_id = "migrated-" + hashlib.sha256((source_digest + MIGRATION_VERSION).encode()).hexdigest()[:24]
        trusted_evidence = trusted_evidence_exists(evidence_refs)
        if (not contract or int(metrics.get("tasks", 0) or 0) < 20 or metrics.get("verified_success_task_rate") in (0, None)
                or not source_report_digest or not trusted_report_exists(str(source_report_digest)) or not trusted_evidence or not proposal.get("concrete_scope")
                or not proposal.get("expected_delta") or not validation.get("fixture_refs")
                or not validation.get("eval_ids") or not validation.get("thresholds")
                or not validation.get("nonregression") or not validation.get("min_samples")):
            measurement_gap.append(source.name)
            if write:
                (output_dir / f"{candidate_id}.json").write_text(json.dumps({
                    "schema_version": SCHEMA_VERSION,
                    "status": "measurement-gap",
                    "legacy_path": str(source),
                    "legacy_digest": source_digest,
                    "converter_version": MIGRATION_VERSION,
                    "skill": skill,
                    "reason": "legacy record lacks trusted source report, evidence, sample threshold, concrete proposal, or validation plan",
                    "before_metrics": metrics,
                }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            continue
        proposal_text = str(legacy.get("change_summary") or legacy.get("action") or proposal.get("concrete_scope"))
        payload = {
            "schema_version": SCHEMA_VERSION,
            "candidate_id": candidate_id,
            "skill": skill,
            "source_report_generated_at": legacy.get("generated_at"),
            "source_report_digest": source_report_digest,
            "converter_version": MIGRATION_VERSION,
            "legacy_path": str(source),
            "target": {"skill_key": skill, **contract},
            "evidence": {"provenance": "legacy-migration", "sample_size": int(metrics.get("tasks", 0)), "evidence_refs": evidence_refs},
            "before_metrics": metrics,
            "proposal": proposal,
            "change_ref": "local:legacy-proposal/" + candidate_id,
            "change_summary": proposal_text,
            "impact": legacy.get("impact") or {},
            "validation_plan": validation,
            "status": "candidate-awaiting-approval",
            "approval_required": True,
            "approval": None,
            "application": None,
            "after_metrics": None,
        }
        valid, reason = validate_candidate(payload)
        if not valid:
            rejected.append(f"{source.name}:{reason}")
            continue
        if write:
            target = output_dir / f"{candidate_id}.json"
            serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            if target.exists():
                if target.read_text(encoding="utf-8") != serialized:
                    rejected.append(f"{source.name}:migration-output-conflict")
                    continue
                skipped.append(candidate_id)
                continue
            target.write_text(serialized, encoding="utf-8")
        converted.append(candidate_id)
        mappings.append({"source": str(source), "output": str(output_dir / f"{candidate_id}.json"), "source_sha256": source_digest})
    result = {"dry_run": not write, "converter_version": MIGRATION_VERSION, "scanned": len(converted) + len(measurement_gap) + len(rejected) + len(skipped), "converted": converted, "already_present": skipped, "measurement_gap": measurement_gap, "rejected": rejected, "mappings": mappings, "output_dir": str(output_dir)}
    if write:
        (output_dir / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
