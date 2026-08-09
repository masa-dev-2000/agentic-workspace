from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from feedback_store import (
    DIRECT_CAPTURE_MODES,
    CONSENT_BASES,
    DIRECTNESS,
    EVIDENCE_ROLES,
    EXPERIMENT_OUTCOMES,
    EXPERIMENT_VERIFICATION,
    EXPLICITNESS,
    IMPACTS,
    OUTCOME_STATUS,
    PRIVACY_CLASSES,
    PURGE_CONFIRMATION,
    RELIABILITY,
    SATISFACTION,
    SOURCE_KINDS,
    SUBJECT_KINDS,
    SURFACES,
    TYPES,
    VALENCES,
    VERIFICATION_CLASS_BY_LEVEL,
    VERIFICATION_EVIDENCE_CLASSES,
    VERIFICATION,
    FeedbackStore,
    PrivacyRepairPendingError,
    StateSafetyError,
)


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def target_hashes(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in values:
        if "=" not in item:
            raise ValueError("target hashes must use TARGET=SHA256")
        target, digest = item.split("=", 1)
        if target in result:
            raise ValueError(f"duplicate target hash: {target}")
        result[target] = digest
    return result


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="feedback-learning", description="Private feedback evidence ledger")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Check database integrity and paths")
    sub.add_parser("status", help="Show collection state and counts")
    drain = sub.add_parser("drain", help="Apply authenticated Hook spool envelopes")
    drain.add_argument("--limit", type=int, default=500)
    drain.add_argument("--max-seconds", type=float, default=1.0)
    add = sub.add_parser("add", help="Record explicit sanitized feedback")
    add.add_argument("--type", choices=sorted(TYPES), required=True)
    add.add_argument("--subject", required=True)
    add.add_argument("--theme-key")
    add.add_argument("--impact", choices=sorted(IMPACTS), default="medium")
    add.add_argument("--explicitness", choices=sorted(EXPLICITNESS), default="explicit")
    add.add_argument("--capture-mode", choices=sorted(DIRECT_CAPTURE_MODES), default="manual")
    add.add_argument("--expectation", default="")
    add.add_argument("--observed", default="")
    add.add_argument("--desired", default="")
    add.add_argument("--session", default="")
    add.add_argument("--turn", default="")
    add.add_argument("--repo", default="")
    add.add_argument("--idempotency-key", default="")
    add.add_argument("--source-kind", choices=sorted(SOURCE_KINDS), default="user")
    add.add_argument("--speaker", default="", help="Pseudonymized before storage")
    add.add_argument("--channel", default="conversation")
    add.add_argument("--subject-kind", choices=sorted(SUBJECT_KINDS), default="unknown")
    add.add_argument("--valence", choices=sorted(VALENCES), default="unknown")
    add.add_argument("--privacy-class", choices=sorted(PRIVACY_CLASSES), default="private")
    add.add_argument("--consent-basis", choices=sorted(CONSENT_BASES))
    add.add_argument("--directness", choices=sorted(DIRECTNESS))
    add.add_argument("--reliability", choices=sorted(RELIABILITY))
    add.add_argument("--raw-ref", default="", help="Optional authorized opaque reference; never raw text")
    add.add_argument("--evidence-role", choices=sorted(EVIDENCE_ROLES), default="support")
    add.add_argument("--persistence-requested", action="store_true")
    events = sub.add_parser("events", help="List recent feedback evidence")
    events.add_argument("--limit", type=int, default=20)
    events.add_argument("--include-text", action="store_true")
    sub.add_parser("rebuild", help="Rebuild themes from immutable evidence")
    review = sub.add_parser("review", help="List recurring themes")
    review.add_argument("--limit", type=int, default=20)
    signals = sub.add_parser("signals", help="Materialize and list immutable LearningSignals")
    signals.add_argument("--limit", type=int, default=20)
    patterns = sub.add_parser("patterns", help="Rebuild and list counter-aware ImprovementPatterns")
    patterns.add_argument("--window-days", type=int, default=90)
    patterns.add_argument("--limit", type=int, default=20)
    propose = sub.add_parser("propose", help="Create a disabled ImprovementProposal and ChangeSet")
    propose.add_argument("pattern_id")
    propose.add_argument("--surface", choices=["auto", *sorted(SURFACES)], default="auto")
    propose.add_argument("--target", action="append", default=[])
    propose.add_argument("--target-hash", action="append", default=[], required=True, metavar="TARGET=SHA256")
    propose.add_argument("--capability-owner", default="")
    propose.add_argument("--title", default="")
    propose.add_argument("--change-summary", default="")
    approve = sub.add_parser("approve-record", help="Record exact expiring human approval; apply nothing")
    approve.add_argument("proposal_id")
    approve.add_argument("--target-hash", action="append", default=[], required=True, metavar="TARGET=SHA256")
    approve.add_argument("--expires-hours", type=int, default=24)
    approve.add_argument("--approval-ref", required=True)
    experiment = sub.add_parser("experiment", help="Consume one approval and start a bounded experiment")
    experiment.add_argument("proposal_id")
    experiment.add_argument("--approval-token", required=True)
    experiment.add_argument("--target-hash", action="append", default=[], required=True, metavar="TARGET=SHA256")
    experiment.add_argument("--hypothesis", required=True)
    evaluate = sub.add_parser("evaluate", help="Append an experiment outcome without claiming causality")
    evaluate.add_argument("experiment_id")
    evaluate.add_argument("--outcome", choices=sorted(EXPERIMENT_OUTCOMES), required=True)
    evaluate.add_argument("--verification", choices=sorted(EXPERIMENT_VERIFICATION), required=True)
    evaluate.add_argument("--evidence-ref", action="append", default=[], required=True)
    evaluate.add_argument("--notes", default="")
    verify_record = sub.add_parser(
        "verify-record",
        help="Bind trusted verification metadata to one running experiment",
    )
    verify_record.add_argument("experiment_id")
    verify_record.add_argument("--evidence-ref", required=True)
    verify_record.add_argument(
        "--outcome",
        choices=sorted(EXPERIMENT_OUTCOMES),
        required=True,
    )
    verify_record.add_argument(
        "--verification",
        choices=sorted(VERIFICATION_CLASS_BY_LEVEL),
        required=True,
    )
    verify_record.add_argument(
        "--evidence-class",
        choices=sorted(VERIFICATION_EVIDENCE_CLASSES),
        required=True,
    )
    proposals = sub.add_parser("proposals", help="List disabled staged proposals")
    proposals.add_argument("--limit", type=int, default=20)
    experiments = sub.add_parser("experiments", help="List experiments and outcomes")
    experiments.add_argument("--limit", type=int, default=20)
    outcome = sub.add_parser("add-outcome", help="Record an intervention outcome")
    outcome.add_argument("feedback_id")
    outcome.add_argument("--action-class", required=True)
    outcome.add_argument("--status", choices=sorted(OUTCOME_STATUS), required=True)
    outcome.add_argument("--verification", choices=sorted(VERIFICATION), required=True)
    outcome.add_argument("--satisfaction", choices=sorted(SATISFACTION), required=True)
    outcome.add_argument("--notes", default="")
    candidates = sub.add_parser("candidates", help="List candidate drafts")
    candidates.add_argument("--limit", type=int, default=20)
    sub.add_parser("disable", help="Disable collection")
    sub.add_parser("enable", help="Enable collection")
    export = sub.add_parser("export", help="Export sanitized evidence locally")
    export.add_argument("--output", required=True)
    purge = sub.add_parser("purge", help="Delete all local feedback-learning state")
    purge.add_argument("--confirm", required=True)
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    store = FeedbackStore()
    try:
        if args.command in {"doctor", "status"}:
            status = store.status()
            emit(status)
            if status.get("health") != "ok":
                return 4
        elif args.command == "drain":
            emit(store.drain_spool(limit=args.limit, max_seconds=args.max_seconds))
        elif args.command == "add":
            feedback_id, inserted = store.add_feedback({
                "feedback_type": args.type, "subject_class": args.subject, "theme_key": args.theme_key,
                "impact": args.impact, "explicitness": args.explicitness, "capture_mode": args.capture_mode,
                "expectation_template": args.expectation, "observed_template": args.observed, "desired_template": args.desired,
                "session_id": args.session, "turn_id": args.turn, "repo": args.repo, "idempotency_key": args.idempotency_key,
                "source_kind": args.source_kind, "speaker_id": args.speaker, "channel": args.channel,
                "subject_kind": args.subject_kind, "valence": args.valence, "privacy_class": args.privacy_class,
                "consent_basis": args.consent_basis, "directness": args.directness, "reliability": args.reliability,
                "raw_ref": args.raw_ref, "evidence_role": args.evidence_role,
                "persistence_requested": args.persistence_requested,
            })
            emit({"feedback_id": feedback_id, "inserted": inserted, "enabled": store.enabled()})
        elif args.command == "events":
            text = ",expectation_template,observed_template,desired_template" if args.include_text else ""
            emit(store.rows(f"""SELECT feedback_id,observed_at,feedback_type,subject_class,theme_key,impact,
                explicitness,capture_mode,source_kind,speaker_hash,channel,subject_kind,valence,
                privacy_class,consent_basis,directness,reliability,raw_ref,evidence_role,
                persistence_requested,provenance_trust{text}
                FROM feedback_events ORDER BY observed_at DESC LIMIT ?""", (max(1, min(args.limit, 500)),)))
        elif args.command == "rebuild":
            emit({"themes_rebuilt": store.rebuild()})
        elif args.command == "review":
            emit(store.rows("SELECT * FROM themes ORDER BY incident_count DESC,last_seen DESC LIMIT ?", (max(1, min(args.limit, 500)),)))
        elif args.command == "signals":
            inserted = store.sync_signals()
            rows = store.rows("SELECT * FROM learning_signals ORDER BY observed_at DESC LIMIT ?", (max(1, min(args.limit, 500)),))
            emit({"materialized": inserted, "signals": rows})
        elif args.command == "patterns":
            rows = store.build_patterns(args.window_days)[:max(1, min(args.limit, 500))]
            for row in rows:
                for field in ("support_refs_json", "counter_refs_json", "boundary_refs_json"):
                    row[field.removesuffix("_json")] = json.loads(row.pop(field))
            emit(rows)
        elif args.command == "propose":
            emit(store.create_proposal(
                args.pattern_id,
                requested_surface=args.surface,
                target_ids=args.target,
                target_hashes=target_hashes(args.target_hash),
                capability_owner=args.capability_owner,
                title=args.title,
                change_summary=args.change_summary,
            ))
        elif args.command == "approve-record":
            if args.expires_hours < 1 or args.expires_hours > 24 * 30:
                raise ValueError("expires-hours must be between 1 and 720")
            emit(store.record_approval(
                args.proposal_id,
                target_hashes(args.target_hash),
                datetime.now(timezone.utc) + timedelta(hours=args.expires_hours),
                args.approval_ref,
            ))
        elif args.command == "experiment":
            emit(store.start_experiment(
                args.proposal_id,
                args.approval_token,
                target_hashes(args.target_hash),
                args.hypothesis,
            ))
        elif args.command == "evaluate":
            emit(store.evaluate_experiment(
                args.experiment_id,
                outcome=args.outcome,
                verification=args.verification,
                evidence_refs=args.evidence_ref,
                notes=args.notes,
            ))
        elif args.command == "verify-record":
            emit(store.record_verification_evidence(
                args.experiment_id,
                evidence_ref=args.evidence_ref,
                outcome=args.outcome,
                verification=args.verification,
                evidence_class=args.evidence_class,
            ))
        elif args.command == "proposals":
            emit(store.rows("""SELECT p.*,c.changeset_id,c.target_hashes_json,c.changeset_hash,c.status changeset_status
                FROM improvement_proposals p JOIN change_sets c ON c.proposal_id=p.proposal_id
                ORDER BY p.created_at DESC LIMIT ?""", (max(1, min(args.limit, 500)),)))
        elif args.command == "experiments":
            emit(store.rows("""SELECT e.*,o.outcome,o.verification,o.evidence_refs_json,o.causal_claim
                FROM experiments e LEFT JOIN experiment_outcomes o ON o.experiment_id=e.experiment_id
                ORDER BY e.started_at DESC LIMIT ?""", (max(1, min(args.limit, 500)),)))
        elif args.command == "add-outcome":
            emit({"outcome_id": store.add_outcome(args.feedback_id, args.action_class, args.status, args.verification, args.satisfaction, args.notes)})
        elif args.command == "candidates":
            emit(store.rows("SELECT * FROM skill_candidates ORDER BY created_at DESC LIMIT ?", (max(1, min(args.limit, 500)),)))
        elif args.command in {"disable", "enable"}:
            store.set_enabled(args.command == "enable")
            emit({"enabled": store.enabled()})
        elif args.command == "export":
            output = Path(args.output).expanduser().resolve()
            payload = {
                "exported_from": str(store.root),
                "events": store.rows("SELECT * FROM feedback_events ORDER BY observed_at"),
                "outcomes": store.rows("SELECT * FROM response_outcomes ORDER BY observed_at"),
                "themes": store.rows("SELECT * FROM themes ORDER BY last_seen"),
                "signals": store.rows("SELECT * FROM learning_signals ORDER BY observed_at"),
                "patterns": store.rows("SELECT * FROM improvement_patterns ORDER BY last_seen"),
                "proposals": store.rows("SELECT * FROM improvement_proposals ORDER BY created_at"),
                "change_sets": store.rows("SELECT * FROM change_sets ORDER BY created_at"),
                "experiments": store.rows("SELECT * FROM experiments ORDER BY started_at"),
                "experiment_outcomes": store.rows("SELECT * FROM experiment_outcomes ORDER BY observed_at"),
                "verification_evidence": store.rows(
                    "SELECT * FROM verification_evidence ORDER BY recorded_at"
                ),
            }
            output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            emit({"output": str(output), "warning": "Sensitive local export; do not transmit without authorization."})
        elif args.command == "purge":
            if args.confirm != PURGE_CONFIRMATION:
                raise ValueError("exact confirmation token required")
            emit(store.purge(args.confirm))
        return 0
    except PrivacyRepairPendingError as exc:
        emit({
            "status": str(exc),
            "retryable": True,
            "action": "close active database readers and retry initialization or drain",
        })
        return 3
    except StateSafetyError as exc:
        emit({
            "health": "error",
            "error_class": exc.error_class,
            "reason": str(exc),
            "root": str(store.root),
        })
        return 4
    except (ValueError, sqlite3.IntegrityError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
