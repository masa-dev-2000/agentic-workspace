from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

from telemetry_store import (
    EVALUATION_OUTCOMES,
    EVALUATION_EVIDENCE_CLASSES,
    EVALUATORS,
    EVIDENCE_CLASSES,
    EVIDENCE_RESULTS,
    FEELING_CLASSES,
    FINAL_STATES,
    MODEL_CLASSES,
    RUBRIC_VERSIONS,
    RUBRIC_CRITERIA,
    SENTIMENTS,
    TelemetryStore,
    process_lock,
)
from task_optimizer import (
    build_report, write_candidate_bundles, _write_candidate_bundles_v2,
    write_latest_report, approve_candidate, apply_candidate, rollback_candidate,
    migrate_legacy_candidates,
)


def emit(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def skill_identity(value: str) -> str:
    try:
        return TelemetryStore._require_identity(
            value, "skill name", limit=160
        )
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def opaque_reference(value: str) -> str:
    if not TelemetryStore._valid_opaque_reference(value):
        raise argparse.ArgumentTypeError(
            "must be an opaque scheme:token reference"
        )
    return value


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="skill-telemetry", description="Private Skill usage telemetry")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("doctor")
    sub.add_parser("status")
    stats = sub.add_parser("stats")
    stats.add_argument("--days", type=int, default=30)
    optimize = sub.add_parser("optimize")
    optimize.add_argument("--days", type=int, default=30)
    optimize.add_argument("--limit", type=int, default=20)
    auto_optimize = sub.add_parser("auto-optimize")
    auto_optimize.add_argument("--days", type=int, default=30)
    auto_optimize.add_argument("--limit", type=int, default=100)
    auto_optimize.add_argument("--drain-limit", type=int, default=500)
    migrate = sub.add_parser("migrate-legacy")
    migrate.add_argument("--limit", type=int, default=500)
    migrate.add_argument("--write", action="store_true", help="write non-destructive migration artifacts; default is dry-run")
    for name in ("candidate-approve", "candidate-apply", "candidate-rollback"):
        command = sub.add_parser(name)
        command.add_argument("path")
        command.add_argument("--candidate-id", required=True)
        command.add_argument("--actor-ref", required=True)
        if name != "candidate-rollback":
            command.add_argument("--approval-ref", required=True)
            command.add_argument("--fingerprint", required=True)
            command.add_argument("--content-digest", required=True)
        if name == "candidate-apply":
            command.add_argument("--changeset-ref", required=True)
        if name == "candidate-rollback":
            command.add_argument("--rollback-ref", required=True)
            command.add_argument("--fingerprint", required=True)
            command.add_argument("--content-digest", required=True)
    runs = sub.add_parser("runs")
    runs.add_argument("--limit", type=int, default=20)
    start = sub.add_parser("start")
    start.add_argument("skill_name", type=skill_identity)
    start.add_argument("--session", default="")
    start.add_argument("--turn", default="")
    start.add_argument("--cwd", default="")
    start.add_argument("--model", choices=sorted(MODEL_CLASSES), default="")
    drain = sub.add_parser("drain")
    drain.add_argument("--limit", type=int, default=500)
    drain.add_argument("--max-seconds", type=float, default=1.0)
    sub.add_parser("reconcile")
    finish = sub.add_parser("finish")
    finish.add_argument("run_id")
    finish.add_argument("--status", choices=sorted(FINAL_STATES), default="returned")
    feedback = sub.add_parser("feedback")
    feedback.add_argument("run_id")
    feedback.add_argument("--sentiment", choices=sorted(SENTIMENTS), required=True)
    feedback.add_argument(
        "--feeling", choices=sorted(FEELING_CLASSES), required=True
    )
    feedback.add_argument("--rating", type=int, choices=range(1, 6))
    sample = sub.add_parser("evaluation-sample")
    sample.add_argument("--skill", action="append", required=True)
    sample.add_argument("--limit", type=int, default=10)
    sample.add_argument("--days", type=int, default=30)
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("run_id")
    evaluate.add_argument("--outcome", choices=sorted(EVALUATION_OUTCOMES), required=True)
    for criterion in sorted(RUBRIC_CRITERIA):
        evaluate.add_argument("--" + criterion.replace("_", "-"), type=int, choices=range(3))
    evaluate.add_argument(
        "--evidence-class",
        action="append",
        choices=sorted(EVALUATION_EVIDENCE_CLASSES),
        default=[],
    )
    evaluate.add_argument(
        "--evidence-ref", action="append", type=opaque_reference, default=[]
    )
    evaluate.add_argument("--evaluator", choices=sorted(EVALUATORS), required=True)
    evaluate.add_argument(
        "--rubric-version",
        choices=sorted(RUBRIC_VERSIONS),
        default="outcome-v1",
    )
    evidence = sub.add_parser("evidence")
    evidence.add_argument("--skill", type=skill_identity, required=True)
    evidence.add_argument("--session", required=True)
    evidence.add_argument("--turn", required=True)
    evidence.add_argument("--cwd", default="")
    evidence.add_argument("--class", dest="evidence_class", choices=sorted(EVIDENCE_CLASSES), required=True)
    evidence.add_argument("--result", choices=sorted(EVIDENCE_RESULTS), required=True)
    evidence.add_argument("--subject", required=True)
    evidence.add_argument("--idempotency-key", default="")
    sub.add_parser("evaluations")
    sub.add_parser("evidence-list")
    return p


def main() -> int:
    argument_parser = parser()
    args = argument_parser.parse_args()
    evaluation_scores = None
    if args.command == "evaluate":
        raw_scores = {
            criterion: getattr(args, criterion)
            for criterion in RUBRIC_CRITERIA
        }
        evaluation_scores = (
            None
            if all(value is None for value in raw_scores.values())
            else raw_scores
        )
        try:
            TelemetryStore.validate_evaluation_contract(
                args.outcome,
                evaluation_scores,
                args.evidence_class,
                args.evidence_ref,
            )
        except ValueError as error:
            argument_parser.error(str(error))
    read_commands = {
        "doctor",
        "status",
        "stats",
        "optimize",
        "runs",
        "evaluation-sample",
        "evaluations",
        "evidence-list",
    }
    if args.command in read_commands:
        store = TelemetryStore(initialize=False)
    else:
        store = TelemetryStore(drain=False)
    if args.command == "init":
        emit({"initialized": True, "status": store.status()})
    elif args.command in {"doctor", "status"}:
        result = store.status()
        if args.command == "doctor":
            result["hooks_file"] = str(store.root.parent / "hooks.json")
            result["hooks_file_exists"] = (store.root.parent / "hooks.json").exists()
            result["journal_mode"] = store.journal_mode()
        emit(result)
    elif args.command == "stats":
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, args.days))).replace(microsecond=0).isoformat()
        groups = store.rows(
            """WITH feedback_by_run AS (
                 SELECT run_id,
                        SUM(CASE WHEN sentiment='positive' THEN 1 ELSE 0 END) positive,
                        SUM(CASE WHEN sentiment='negative' THEN 1 ELSE 0 END) negative,
                        SUM(CASE WHEN sentiment='mixed' THEN 1 ELSE 0 END) mixed
                 FROM skill_feedback GROUP BY run_id
               )
               SELECT r.skill_key,r.skill_fingerprint,r.provenance_trust,r.detection,
                      COUNT(*) runs,
                      SUM(CASE WHEN r.status='returned' THEN 1 ELSE 0 END) returned,
                      SUM(CASE WHEN r.status='failed' THEN 1 ELSE 0 END) failed,
                      SUM(CASE WHEN r.status='interrupted' THEN 1 ELSE 0 END) interrupted,
                      SUM(CASE WHEN r.duration_quality='exact' THEN 1 ELSE 0 END) exact_durations,
                      SUM(CASE WHEN r.duration_quality='bounded' THEN 1 ELSE 0 END) bounded_durations,
                      SUM(CASE WHEN r.duration_quality='unknown' THEN 1 ELSE 0 END) unknown_durations,
                      SUM(CASE WHEN r.duration_quality='pending' THEN 1 ELSE 0 END) pending_durations,
                      ROUND(AVG(CASE WHEN r.duration_quality='exact' THEN r.duration_ms END),0)
                        average_exact_duration_ms,
                      ROUND(AVG(CASE WHEN r.duration_quality='bounded' THEN r.duration_ms END),0)
                        average_bounded_duration_upper_ms,
                      SUM(r.tool_failure_count) tool_failures,
                      SUM(COALESCE(f.positive,0)) positive,
                      SUM(COALESCE(f.negative,0)) negative,
                      SUM(COALESCE(f.mixed,0)) mixed
               FROM skill_runs r
               LEFT JOIN feedback_by_run f ON f.run_id=r.run_id
               WHERE r.started_at>=?
               GROUP BY r.skill_key,r.skill_fingerprint,r.provenance_trust,r.detection
               ORDER BY runs DESC,r.skill_key,r.skill_fingerprint,
                        r.provenance_trust,r.detection""",
            (cutoff,),
        )
        emit({"freshness": store.freshness(), "groups": groups})
    elif args.command == "optimize":
        emit(build_report(store, args.days, args.limit))
    elif args.command == "auto-optimize":
        try:
            with process_lock(store.root / "optimizer.lock", timeout=0.1):
                drain_result = {"processed": 0, "duplicate": 0, "deferred": 0, "rejected": 0}
                for _ in range(5):
                    batch = store.drain_spool(
                        limit=max(1, min(args.drain_limit, 5000)), max_seconds=1.0
                    )
                    for key, value in batch.items():
                        drain_result[key] += value
                    if not batch.get("deferred") and not batch.get("processed"):
                        break
                report = write_latest_report(
                    store, args.days, args.limit, store.freshness(), drain_result
                )
                candidates = _write_candidate_bundles_v2(store, report)
                emit({"drain": drain_result, "report": report, "candidates": candidates})
        except TimeoutError:
            emit({"skipped": "optimizer-already-running"})
    elif args.command == "migrate-legacy":
        emit(migrate_legacy_candidates(store.root, args.limit, args.write))
    elif args.command in {"candidate-approve", "candidate-apply", "candidate-rollback"}:
        if args.command == "candidate-rollback":
            candidate_data = json.loads(open(args.path, encoding="utf-8").read())
            target = {"skill_key": candidate_data.get("target", {}).get("skill_key", ""), "contractFingerprint": args.fingerprint, "contractContentDigest": args.content_digest}
            emit(rollback_candidate(args.path, args.candidate_id, target, args.rollback_ref, args.actor_ref))
        else:
            target = {"skill_key": json.loads(open(args.path, encoding="utf-8").read()).get("target", {}).get("skill_key", ""), "contractFingerprint": args.fingerprint, "contractContentDigest": args.content_digest}
            if args.command == "candidate-approve":
                emit(approve_candidate(args.path, args.candidate_id, target, args.approval_ref, args.actor_ref))
            else:
                emit(apply_candidate(args.path, args.candidate_id, target, args.approval_ref, args.changeset_ref, args.actor_ref))
    elif args.command == "runs":
        emit(store.rows(
            """SELECT r.run_id,r.skill_key,r.skill_fingerprint,r.provider,
                      r.provenance_trust,r.detection,r.status,r.started_at,
                      r.duration_ms,r.duration_quality,r.end_reason,
                      r.tool_failure_count,
                      GROUP_CONCAT(f.sentiment) sentiment
               FROM skill_runs r LEFT JOIN skill_feedback f ON f.run_id=r.run_id
               GROUP BY r.run_id ORDER BY r.started_at DESC LIMIT ?""",
            (max(1, min(args.limit, 500)),),
        ))
    elif args.command == "start":
        emit({"run_id": store.start_manual(args.skill_name, args.session, args.turn, args.cwd, args.model)})
    elif args.command == "drain":
        emit({
            "spool": store.drain_spool(
                limit=max(1, min(args.limit, 5000)),
                max_seconds=max(0.05, min(args.max_seconds, 30.0)),
            ),
            "status": store.status(),
        })
    elif args.command == "reconcile":
        emit({
            "spool": store.drain_spool(),
            "proven_orphans_interrupted": store.recover_proven_orphans(),
            "stale_interrupted": store.recover_stale(),
            "status": store.status(),
        })
    elif args.command == "finish":
        emit({"run_id": args.run_id, "finished": store.finish_run(args.run_id, args.status), "status": args.status})
    elif args.command == "feedback":
        emit({"feedback_id": store.add_feedback(args.run_id, args.sentiment, args.feeling, args.rating)})
    elif args.command == "evaluation-sample":
        emit({
            skill: store.evaluation_sample(skill, args.limit, args.days)
            for skill in args.skill
        })
    elif args.command == "evaluate":
        emit({"evaluation_id": store.add_evaluation(
            args.run_id, args.outcome, evaluation_scores,
            args.evidence_class, args.evidence_ref,
            args.evaluator, args.rubric_version,
        )})
    elif args.command == "evidence":
        emit(store.add_evidence(
            {"session_id": args.session, "turn_id": args.turn, "cwd": args.cwd},
            args.evidence_class, args.result, args.subject,
            detection="explicit-manual", skill_key=args.skill,
            idempotency_hint=args.idempotency_key,
        ))
    elif args.command == "evaluations":
        emit(store.rows(
            """SELECT e.evaluation_id,e.run_id,r.skill_key,e.rubric_version,e.outcome,
                      e.total_score,e.evidence_classes,e.evidence_refs,e.evaluator,e.reviewed_at
               FROM skill_evaluations e JOIN skill_runs r ON r.run_id=e.run_id
               ORDER BY e.reviewed_at DESC,e.evaluation_id"""
        ))
    elif args.command == "evidence-list":
        emit(store.rows(
            """SELECT e.evidence_id,e.evidence_class,e.result,e.detection,e.observed_at,
                      COUNT(l.run_id) linked_runs
               FROM skill_evidence e
               LEFT JOIN skill_run_evidence l ON l.evidence_id=e.evidence_id
               GROUP BY e.evidence_id ORDER BY e.observed_at DESC,e.evidence_id"""
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
