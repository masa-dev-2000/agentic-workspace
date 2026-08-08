from __future__ import annotations

import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import configure_hooks
import telemetry_cli
from telemetry_store import (
    COMPONENT_VERSION,
    PLUGIN_MANIFEST_LIMIT,
    PRIVACY_REPAIR_PENDING,
    PRIVACY_REPAIR_VERSION,
    PrivacyRepairPendingError,
    TelemetryStore,
    utc_now,
)


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "state"
        self.skill = Path(__file__).resolve().parents[1]
        self.store = TelemetryStore(self.root)
        self.event = {
            "session_id": "s1", "turn_id": "t1", "cwd": str(self.skill.parent),
            "model": "test", "hook_event_name": "PostToolUse",
            "tool_input": {"command": f'Get-Content -Raw "{self.skill / "SKILL.md"}"'},
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_detect_dedupe_finish_feedback(self):
        paths = self.store.skill_paths(self.event["tool_input"])
        self.assertEqual([self.skill / "SKILL.md"], paths)
        first = self.store.start_from_path(paths[0], self.event)
        second = self.store.start_from_path(paths[0], self.event)
        self.assertEqual(first, second)
        stop = dict(self.event, hook_event_name="Stop")
        self.assertEqual(1, self.store.finish_turn(stop))
        prompt = dict(self.event, hook_event_name="UserPromptSubmit", turn_id="t2", prompt="いいね。")
        self.assertEqual(1, self.store.attach_reaction(prompt))
        status = self.store.status()
        self.assertEqual(1, status["counts"]["runs"])
        self.assertEqual(1, status["counts"]["returned"])
        self.assertEqual(1, status["counts"]["feedback"])

    def test_repeated_skill_path_is_authorized_once_per_hook_event(self):
        skill_file = self.skill / "SKILL.md"
        for repetitions in (1, 20, 100):
            event = dict(
                self.event,
                tool_input={
                    "command": " ".join(
                        f'Get-Content -Raw "{skill_file}"'
                        for _ in range(repetitions)
                    )
                },
            )
            with (
                mock.patch.object(
                    self.store,
                    "_canonical_local_sources",
                    wraps=self.store._canonical_local_sources,
                ) as registry_lookup,
                mock.patch.object(
                    self.store,
                    "_identity",
                    wraps=self.store._identity,
                ) as identity_lookup,
            ):
                started = time.monotonic()
                record = self.store.sanitize_hook_event(event)
                elapsed = time.monotonic() - started
            with self.subTest(repetitions=repetitions):
                self.assertEqual(1, len(record["skills"]))
                self.assertEqual(1, registry_lookup.call_count)
                self.assertEqual(1, identity_lookup.call_count)
                self.assertLess(elapsed, 1.5)

    def test_ordinary_prompt_is_unrated(self):
        self.assertIsNone(self.store.classify_sentiment("次の作業を始めてください"))
        run = self.store.start_from_path(self.skill / "SKILL.md", self.event)
        self.store.finish_run(run, "returned")
        ordinary = dict(
            self.event, hook_event_name="UserPromptSubmit", turn_id="ordinary-next",
            prompt="次の作業を始めてください",
        )
        self.assertEqual(0, self.store.attach_reaction(ordinary))
        self.assertEqual(0, self.store.status()["counts"]["evidence"])

    def test_explicit_reaction_creates_linked_private_evidence(self):
        cases = [
            ("いいね。", "passed"),
            ("違う、修正して。", "failed"),
            ("いいね。違う。", "ambiguous"),
        ]
        for index, (prompt, expected_result) in enumerate(cases):
            with self.subTest(prompt=prompt):
                case_root = Path(self.tmp.name) / f"reaction-state-{index}"
                store = TelemetryStore(case_root)
                base = dict(self.event, session_id=f"reaction-session-{index}", turn_id="work")
                run = store.start_from_path(self.skill / "SKILL.md", base)
                store.finish_run(run, "returned")
                reaction = dict(base, turn_id="reaction", prompt=prompt)
                self.assertEqual(1, store.attach_reaction(reaction))
                evidence = store.rows(
                    """SELECT e.evidence_class,e.result,e.subject_hash,l.run_id
                       FROM skill_evidence e
                       JOIN skill_run_evidence l ON l.evidence_id=e.evidence_id"""
                )[0]
                self.assertEqual("explicit-feedback", evidence["evidence_class"])
                self.assertEqual(expected_result, evidence["result"])
                self.assertEqual(run, evidence["run_id"])
                self.assertNotIn(prompt, store.db_path.read_bytes().decode("latin-1"))
                # Reprocessing the same hook remains idempotent for evidence and links.
                store.attach_reaction(reaction)
                self.assertEqual(1, store.status()["counts"]["evidence"])
                self.assertEqual(1, store.status()["counts"]["evidence_links"])

    def test_late_prompt_cannot_attach_feedback_to_a_future_run(self):
        session = "late-prompt-session"
        old_event = dict(self.event, session_id=session, turn_id="old-work")
        future_event = dict(self.event, session_id=session, turn_id="future-work")
        old_run = self.store.start_from_path(self.skill / "SKILL.md", old_event)
        future_run = self.store.start_from_path(
            self.skill / "SKILL.md", future_event
        )
        self.store.finish_run(old_run, "returned")
        self.store.finish_run(future_run, "returned")
        with self.store.connection() as db:
            db.execute(
                "UPDATE skill_runs SET ended_at=? WHERE run_id=?",
                ("2026-01-01T10:00:00+00:00", old_run),
            )
            db.execute(
                "UPDATE skill_runs SET ended_at=? WHERE run_id=?",
                ("2026-01-02T10:00:00+00:00", future_run),
            )
        prompt = dict(
            self.event,
            session_id=session,
            turn_id="reaction",
            hook_event_name="UserPromptSubmit",
            prompt="ナイス！",
        )
        record = self.store.sanitize_hook_event(prompt)
        record["observed_at"] = "2026-01-01T12:00:00+00:00"
        with self.store.connection() as db:
            self.store._apply_spool_record(db, record)
        linked = self.store.rows(
            "SELECT run_id FROM skill_feedback ORDER BY run_id"
        )
        self.assertEqual([old_run], [row["run_id"] for row in linked])

    def test_same_second_delayed_prompt_holds_future_run(self):
        session = "same-second-session"
        old_run = self.store.start_manual(
            "old-skill", session, "old-work", "repo", "test"
        )
        future_run = self.store.start_manual(
            "future-skill", session, "future-work", "repo", "test"
        )
        self.store.finish_run(old_run, "returned")
        self.store.finish_run(future_run, "returned")
        with self.store.connection() as db:
            db.execute(
                """UPDATE skill_runs
                   SET started_at=?,ended_at=?,duration_ms=1000,
                       status='returned',end_reason='manual-returned',
                       duration_quality='exact'
                   WHERE run_id=?""",
                (
                    "2026-01-01T09:59:58+00:00",
                    "2026-01-01T09:59:59+00:00",
                    old_run,
                ),
            )
            db.execute(
                """UPDATE skill_runs
                   SET started_at=?,ended_at=?,duration_ms=0,
                       status='returned',end_reason='manual-returned',
                       duration_quality='exact'
                   WHERE run_id=?""",
                (
                    "2026-01-01T10:00:00+00:00",
                    "2026-01-01T10:00:00+00:00",
                    future_run,
                ),
            )
        prompt = {
            "session_id": session,
            "turn_id": "old-reaction",
            "cwd": "repo",
            "model": "test",
            "hook_event_name": "UserPromptSubmit",
            "prompt": "ナイス！",
        }
        record = self.store.sanitize_hook_event(prompt)
        record["observed_at"] = "2026-01-01T10:00:00+00:00"
        record["event_id"] = "a" * 64
        with self.store.connection() as db:
            self.store._apply_spool_record(db, record)
        linked = self.store.rows(
            "SELECT run_id FROM skill_feedback ORDER BY run_id"
        )
        self.assertEqual([old_run], [row["run_id"] for row in linked])
        future = self.store.rows(
            """SELECT status,end_reason FROM skill_runs WHERE run_id=?""",
            (future_run,),
        )[0]
        self.assertEqual("returned", future["status"])
        self.assertEqual("manual-returned", future["end_reason"])

    def test_new_hook_timestamps_use_microseconds_and_legacy_seconds_validate(self):
        now = utc_now()
        self.assertRegex(now, r"\.\d{6}\+00:00$")
        legacy = self.store.sanitize_hook_event(
            dict(self.event, hook_event_name="Stop")
        )
        legacy["observed_at"] = "2026-01-01T10:00:00+00:00"
        self.assertIsNotNone(self.store._record_time(legacy))
        micro = dict(legacy, observed_at="2026-01-01T10:00:00.000001+00:00")
        self.assertIsNotNone(self.store._record_time(micro))
        invalid = dict(legacy, observed_at="2026-01-01T10:00:00.1+00:00")
        with self.assertRaisesRegex(ValueError, "canonical UTC"):
            self.store._record_time(invalid)

    def test_failure_does_not_mark_run_failed(self):
        run = self.store.start_from_path(self.skill / "SKILL.md", self.event)
        self.store.increment_failures(self.event)
        self.store.finish_turn(dict(self.event, hook_event_name="Stop"))
        row = self.store.rows("SELECT status,tool_failure_count FROM skill_runs WHERE run_id=?", (run,))[0]
        self.assertEqual("returned", row["status"])
        self.assertEqual(1, row["tool_failure_count"])

    def test_evidence_links_only_unique_run_and_domain_verdict_is_explicit(self):
        payload = {
            "session_id": "shared",
            "turn_id": "shared",
            "cwd": str(self.skill.parent),
        }
        first = self.store.start_manual(
            "first-skill", session_id="shared", turn_id="shared"
        )
        self.store.start_manual(
            "second-skill", session_id="shared", turn_id="shared"
        )
        ambiguous = self.store.add_evidence(
            payload, "test", "passed", "shared test"
        )
        self.assertEqual(0, ambiguous["linked_runs"])
        with self.assertRaises(ValueError):
            self.store.add_evidence(
                payload, "domain-verdict", "passed", "accepted"
            )
        verdict = self.store.add_evidence(
            payload,
            "domain-verdict",
            "passed",
            "accepted",
            detection="explicit-manual",
            skill_key="first-skill",
        )
        self.assertEqual(1, verdict["linked_runs"])
        linked = self.store.rows(
            """SELECT l.run_id,e.evidence_class,e.provenance_trust,e.detection
               FROM skill_run_evidence l
               JOIN skill_evidence e ON e.evidence_id=l.evidence_id"""
        )[0]
        self.assertEqual(first, linked["run_id"])
        self.assertEqual("domain-verdict", linked["evidence_class"])
        self.assertEqual("trusted", linked["provenance_trust"])
        self.assertEqual("explicit-manual", linked["detection"])

    def test_manual_lifecycle(self):
        run = self.store.start_manual("manual-skill")
        self.assertTrue(self.store.finish_run(run, "failed"))
        feedback = self.store.add_feedback(
            run, "negative", "explicit-complaint-or-correction", 2
        )
        self.assertTrue(feedback.startswith("skillfb_"))

    def test_cli_start_does_not_drain_pending_spool(self):
        root = Path(self.tmp.name) / "no-implicit-drain"
        store = TelemetryStore(root, drain=False)
        queued = store.spool_hook_event(
            {
                "session_id": "pending-session",
                "turn_id": "pending-turn",
                "cwd": str(self.skill),
                "model": "test",
                "hook_event_name": "Stop",
            }
        )
        self.assertIsNotNone(queued)
        argv = ["telemetry_cli.py", "start", "manual-skill"]
        with (
            mock.patch.dict(
                os.environ,
                {"CODEX_SKILL_TELEMETRY_HOME": str(root)},
            ),
            mock.patch.object(sys, "argv", argv),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(0, telemetry_cli.main())
        reopened = TelemetryStore(root, drain=False)
        self.assertEqual(1, reopened.spool_status()["pending"])
        self.assertEqual(1, reopened.status()["counts"]["runs"])
        with self.assertRaises(ValueError):
            TelemetryStore(root, drain=True)

    def test_cli_rejects_free_text_privacy_metadata(self):
        invalid_cases = [
            ["start", "safe-skill", "--model", "raw model body"],
            [
                "feedback",
                "skillrun_" + "0" * 32,
                "--sentiment",
                "positive",
                "--feeling",
                "helpful free text",
            ],
            [
                "evaluate",
                "skillrun_" + "0" * 32,
                "--outcome",
                "unverified",
                "--evidence-ref",
                "ordinary sentence body",
                "--evaluator",
                "codex",
            ],
        ]
        for arguments in invalid_cases:
            with self.subTest(arguments=arguments):
                with (
                    redirect_stderr(io.StringIO()),
                    self.assertRaises(SystemExit),
                ):
                    telemetry_cli.parser().parse_args(arguments)

    def test_cli_drain_accepts_bounded_maintenance_budget(self):
        args = telemetry_cli.parser().parse_args(
            ["drain", "--limit", "2000", "--max-seconds", "20"]
        )
        self.assertEqual(2000, args.limit)
        self.assertEqual(20.0, args.max_seconds)

    def test_evaluation_sample_and_outcome_record(self):
        run_ids = []
        for index in range(12):
            event = dict(self.event, turn_id=f"sample-{index}")
            run = self.store.start_from_path(self.skill / "SKILL.md", event)
            if index in {2, 5, 8}:
                self.store.increment_failures(event)
            if index in {3, 7}:
                self.store.finish_run(run, "interrupted")
            else:
                self.store.finish_run(run, "returned")
            run_ids.append(run)
        sample = self.store.evaluation_sample("skill-telemetry", 10, 30)
        self.assertEqual(10, len(sample))
        self.assertEqual(len(sample), len({item["run_id"] for item in sample}))
        verified_event = dict(self.event, turn_id="verified-evaluation")
        verified_run = self.store.start_from_path(
            self.skill / "SKILL.md", verified_event
        )
        self.store.finish_run(verified_run, "returned")
        verified_refs = []
        for evidence_class in (
            "artifact",
            "authority",
            "domain-verdict",
            "test",
        ):
            evidence = self.store.add_evidence(
                verified_event,
                evidence_class,
                "passed",
                f"{evidence_class}-verification",
                detection="explicit-manual",
            )
            verified_refs.append(f"evidence:{evidence['evidence_id']}")
        evaluation = self.store.add_evaluation(
            verified_run,
            "verified-success",
            {
                "outcome_achieved": 2,
                "completion_evidence": 2,
                "authority_safety": 2,
                "avoidable_rework": 1,
                "efficient_recoverable": 1,
            },
            ["artifact", "authority", "domain-verdict", "test"],
            verified_refs,
            "unit-test",
        )
        self.assertTrue(evaluation.startswith("skilleval_"))
        row = self.store.rows(
            """SELECT outcome,total_score,evidence_refs
               FROM skill_evaluations"""
        )[0]
        self.assertEqual("verified-success", row["outcome"])
        self.assertEqual(8, row["total_score"])
        refs = json.loads(row["evidence_refs"])
        self.assertEqual(4, len(refs))
        self.assertTrue(
            all(re.fullmatch(r"evidence:[0-9a-f]{64}", ref) for ref in refs)
        )
        self.assertNotIn(
            verified_refs[0].split(":", 1)[1],
            row["evidence_refs"],
        )

    def test_verified_success_rejects_invented_unlinked_evidence_refs(self):
        run = self.store.start_from_path(self.skill / "SKILL.md", self.event)
        self.store.finish_run(run, "returned")
        scores = {
            "outcome_achieved": 2,
            "completion_evidence": 2,
            "authority_safety": 2,
            "avoidable_rework": 1,
            "efficient_recoverable": 1,
        }
        with self.assertRaisesRegex(ValueError, "linked to the evaluated run"):
            self.store.add_evaluation(
                run,
                "verified-success",
                scores,
                ["authority", "domain-verdict", "test"],
                [
                    "evidence:skillevidence_" + "a" * 32,
                    "evidence:skillevidence_" + "b" * 32,
                    "evidence:skillevidence_" + "c" * 32,
                ],
                "unit-test",
            )

    def test_verified_success_requires_returned_proven_terminal_run(self):
        scores = {
            "outcome_achieved": 2,
            "completion_evidence": 2,
            "authority_safety": 2,
            "avoidable_rework": 1,
            "efficient_recoverable": 1,
        }
        for index, state in enumerate(
            ("running", "failed", "interrupted", "legacy-returned")
        ):
            with self.subTest(state=state):
                store = TelemetryStore(
                    Path(self.tmp.name) / f"terminal-{index}"
                )
                session = f"terminal-session-{index}"
                turn = f"terminal-turn-{index}"
                skill = f"terminal-skill-{index}"
                event = {
                    "session_id": session,
                    "turn_id": turn,
                    "cwd": str(self.skill.parent),
                }
                run = store.start_manual(
                    skill, session, turn, event["cwd"], "test"
                )
                if state in {"failed", "interrupted"}:
                    store.finish_run(run, state)
                elif state == "legacy-returned":
                    store.finish_run(run, "returned")
                    with store.connection() as db:
                        db.execute(
                            """UPDATE skill_runs
                               SET end_reason='legacy-unknown',
                                   duration_quality='unknown'
                               WHERE run_id=?""",
                            (run,),
                        )
                refs = []
                for evidence_class in (
                    "test",
                    "authority",
                    "domain-verdict",
                ):
                    evidence = store.add_evidence(
                        event,
                        evidence_class,
                        "passed",
                        evidence_class,
                        detection="explicit-manual",
                        skill_key=skill,
                        idempotency_hint=evidence_class,
                    )
                    refs.append(f"evidence:{evidence['evidence_id']}")
                with self.assertRaisesRegex(
                    ValueError, "verified terminal reason"
                ):
                    store.add_evaluation(
                        run,
                        "verified-success",
                        scores,
                        ["test", "authority", "domain-verdict"],
                        refs,
                        "unit-test",
                    )

    def test_verified_success_rejects_every_nonqualifying_reference(self):
        event = {
            "session_id": "strict-ref-session",
            "turn_id": "strict-ref-target",
            "cwd": str(self.skill.parent),
        }
        skill = "strict-ref-skill"
        run = self.store.start_manual(
            skill,
            event["session_id"],
            event["turn_id"],
            event["cwd"],
            "test",
        )
        self.store.finish_run(run, "returned")
        scores = {
            "outcome_achieved": 2,
            "completion_evidence": 2,
            "authority_safety": 2,
            "avoidable_rework": 1,
            "efficient_recoverable": 1,
        }
        valid_refs = []
        for evidence_class in ("test", "authority", "domain-verdict"):
            evidence = self.store.add_evidence(
                event,
                evidence_class,
                "passed",
                "valid-" + evidence_class,
                detection="explicit-manual",
                skill_key=skill,
                idempotency_hint="valid-" + evidence_class,
            )
            valid_refs.append(f"evidence:{evidence['evidence_id']}")

        failed = self.store.add_evidence(
            event,
            "authority",
            "failed",
            "authority-denied",
            detection="explicit-manual",
            skill_key=skill,
            idempotency_hint="authority-denied",
        )
        legacy = self.store.add_evidence(
            event,
            "artifact",
            "passed",
            "legacy-artifact",
            detection="explicit-manual",
            skill_key=skill,
            idempotency_hint="legacy-artifact",
        )
        with self.store.connection() as db:
            db.execute(
                """UPDATE skill_evidence SET provenance_trust='legacy-unverified'
                   WHERE evidence_id=?""",
                (legacy["evidence_id"],),
            )

        other_event = dict(event, turn_id="strict-ref-other")
        other = self.store.start_manual(
            "strict-ref-other-skill",
            other_event["session_id"],
            other_event["turn_id"],
            other_event["cwd"],
            "test",
        )
        self.store.finish_run(other, "returned")
        cross = self.store.add_evidence(
            other_event,
            "artifact",
            "passed",
            "cross-run-artifact",
            detection="explicit-manual",
            skill_key="strict-ref-other-skill",
            idempotency_hint="cross-run-artifact",
        )

        invalid_cases = [
            (
                "non-evidence-scheme",
                ["test", "authority", "domain-verdict"],
                valid_refs + ["artifact:opaque123"],
            ),
            (
                "invented",
                ["test", "authority", "domain-verdict"],
                valid_refs
                + ["evidence:skillevidence_" + "f" * 32],
            ),
            (
                "failed",
                ["test", "authority", "domain-verdict"],
                valid_refs + [f"evidence:{failed['evidence_id']}"],
            ),
            (
                "legacy",
                ["test", "authority", "domain-verdict", "artifact"],
                valid_refs + [f"evidence:{legacy['evidence_id']}"],
            ),
            (
                "cross-run",
                ["test", "authority", "domain-verdict", "artifact"],
                valid_refs + [f"evidence:{cross['evidence_id']}"],
            ),
        ]
        for label, classes, refs in invalid_cases:
            with self.subTest(label=label), self.assertRaises(ValueError):
                self.store.add_evaluation(
                    run,
                    "verified-success",
                    scores,
                    classes,
                    refs,
                    "unit-test",
                )

    def test_manual_evidence_never_fans_out_and_old_fanout_is_ambiguous(self):
        shared = {
            "session_id": "fanout-session",
            "turn_id": "fanout-turn",
            "cwd": str(self.skill.parent),
        }
        first = self.store.start_manual(
            "fanout-skill", "fanout-session", "fanout-turn"
        )
        second = self.store.start_manual(
            "fanout-skill", "fanout-session", "fanout-turn"
        )
        ambiguous = self.store.add_evidence(
            shared,
            "domain-verdict",
            "passed",
            "single-verdict",
            detection="explicit-manual",
            skill_key="fanout-skill",
            idempotency_hint="single-verdict",
        )
        self.assertEqual(0, ambiguous["linked_runs"])
        self.assertEqual(
            [],
            self.store.rows(
                """SELECT run_id FROM skill_run_evidence
                   WHERE evidence_id=?""",
                (ambiguous["evidence_id"],),
            ),
        )
        self.store.finish_run(first, "returned")
        self.store.finish_run(second, "returned")

        target_event = dict(shared, turn_id="fanout-target")
        target = self.store.start_manual(
            "fanout-target",
            "fanout-session",
            "fanout-target",
            target_event["cwd"],
            "test",
        )
        other = self.store.start_manual(
            "fanout-other",
            "fanout-session",
            "fanout-other",
            target_event["cwd"],
            "test",
        )
        self.store.finish_run(target, "returned")
        self.store.finish_run(other, "returned")
        refs = []
        for evidence_class in ("test", "authority", "domain-verdict"):
            evidence = self.store.add_evidence(
                target_event,
                evidence_class,
                "passed",
                evidence_class,
                detection="explicit-manual",
                skill_key="fanout-target",
                idempotency_hint="fanout-" + evidence_class,
            )
            refs.append(f"evidence:{evidence['evidence_id']}")
        fanout_evidence_id = refs[0].split(":", 1)[1]
        with self.store.connection() as db:
            db.execute(
                """INSERT INTO skill_run_evidence(run_id,evidence_id,linked_at)
                   VALUES(?,?,?)""",
                (other, fanout_evidence_id, utc_now()),
            )
        with self.assertRaisesRegex(ValueError, "evaluated run only"):
            self.store.add_evaluation(
                target,
                "verified-success",
                {
                    "outcome_achieved": 2,
                    "completion_evidence": 2,
                    "authority_safety": 2,
                    "avoidable_rework": 1,
                    "efficient_recoverable": 1,
                },
                ["test", "authority", "domain-verdict"],
                refs,
                "unit-test",
            )

    def test_unverified_evaluation_does_not_invent_scores(self):
        run = self.store.start_from_path(self.skill / "SKILL.md", self.event)
        self.store.finish_run(run, "returned")
        self.store.add_evaluation(run, "unverified", None, ["lifecycle"], ["run:opaque"], "unit-test")
        row = self.store.rows("SELECT outcome,total_score FROM skill_evaluations")[0]
        self.assertEqual("unverified", row["outcome"])
        self.assertIsNone(row["total_score"])

    def test_evaluation_cross_field_invariants_fail_closed(self):
        run = self.store.start_manual("safe-skill")
        self.store.finish_run(run, "returned")
        base = {
            "outcome_achieved": 1,
            "completion_evidence": 1,
            "authority_safety": 1,
            "avoidable_rework": 1,
            "efficient_recoverable": 1,
        }
        invalid = [
            ("unverified", base, ["test"], ["test:opaque123"]),
            ("partial", base, [], ["test:opaque123"]),
            ("partial", base, ["test"], []),
            (
                "partial",
                dict(base, outcome_achieved=2),
                ["authority"],
                ["run:opaque123"],
            ),
            (
                "partial",
                dict(base, completion_evidence=2),
                ["authority"],
                ["run:opaque123"],
            ),
            (
                "partial",
                dict(base, authority_safety=2),
                ["test"],
                ["test:opaque123"],
            ),
            (
                "partial",
                dict(base, avoidable_rework=2),
                ["test"],
                ["test:opaque123"],
            ),
            (
                "partial",
                dict(base, efficient_recoverable=2),
                ["test"],
                ["test:opaque123"],
            ),
            (
                "partial",
                dict(
                    base,
                    outcome_achieved=2,
                    completion_evidence=2,
                    authority_safety=2,
                ),
                ["authority", "test"],
                ["test:opaque123"],
            ),
            (
                "partial",
                dict(base, outcome_achieved=0),
                ["test"],
                ["test:opaque123"],
            ),
            (
                "rework-required",
                dict(base, completion_evidence=2),
                ["test"],
                ["test:opaque123"],
            ),
            (
                "rejected",
                dict(base, outcome_achieved=0),
                ["test"],
                ["test:opaque123"],
            ),
            (
                "rejected",
                dict(base, outcome_achieved=1, authority_safety=0),
                ["test"],
                ["test:opaque123"],
            ),
        ]
        for outcome, scores, classes, refs in invalid:
            with self.subTest(
                outcome=outcome,
                scores=scores,
                classes=classes,
                refs=refs,
            ), self.assertRaises(ValueError):
                self.store.add_evaluation(
                    run,
                    outcome,
                    scores,
                    classes,
                    refs,
                    "unit-test",
                )
        self.assertEqual(
            0, self.store.rows("SELECT COUNT(*) count FROM skill_evaluations")[0]["count"]
        )

    def test_cli_evaluation_invariant_rejects_before_store_initialization(self):
        argv = [
            "telemetry_cli.py",
            "evaluate",
            "skillrun_" + "0" * 32,
            "--outcome",
            "partial",
            "--outcome-achieved",
            "2",
            "--completion-evidence",
            "2",
            "--authority-safety",
            "2",
            "--avoidable-rework",
            "1",
            "--efficient-recoverable",
            "1",
            "--evidence-class",
            "authority",
            "--evidence-class",
            "test",
            "--evidence-ref",
            "test:opaque123",
            "--evaluator",
            "unit-test",
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(
                TelemetryStore,
                "__init__",
                side_effect=AssertionError("store must not initialize"),
            ) as constructor,
            redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            telemetry_cli.main()
        self.assertEqual(2, raised.exception.code)
        constructor.assert_not_called()

    def test_new_prompt_interrupts_prior_turn_only(self):
        first = self.store.start_from_path(self.skill / "SKILL.md", self.event)
        next_event = dict(self.event, turn_id="t2")
        second = self.store.start_from_path(self.skill / "SKILL.md", next_event)
        self.assertEqual(1, self.store.recover_superseded(next_event))
        rows = {
            row["run_id"]: row
            for row in self.store.rows(
                """SELECT run_id,status,duration_ms,end_reason,
                          duration_quality FROM skill_runs"""
            )
        }
        self.assertEqual("interrupted", rows[first]["status"])
        self.assertIsNotNone(rows[first]["duration_ms"])
        self.assertEqual("superseded", rows[first]["end_reason"])
        self.assertEqual("exact", rows[first]["duration_quality"])
        self.assertEqual("running", rows[second]["status"])

    def test_bound_manual_run_finishes_with_turn(self):
        run = self.store.start_manual("manual-skill", session_id="s1", turn_id="t1")
        self.assertEqual(1, self.store.finish_turn(dict(self.event, hook_event_name="Stop")))
        row = self.store.rows(
            """SELECT status,end_reason,duration_quality
               FROM skill_runs WHERE run_id=?""",
            (run,),
        )[0]
        self.assertEqual("returned", row["status"])
        self.assertEqual("stop", row["end_reason"])
        self.assertEqual("exact", row["duration_quality"])

    def test_reconcile_interrupts_proven_orphan(self):
        first = self.store.start_from_path(self.skill / "SKILL.md", self.event)
        next_event = dict(self.event, turn_id="t2")
        self.store.start_from_path(self.skill / "SKILL.md", next_event)
        self.store.finish_turn(dict(next_event, hook_event_name="Stop"))
        self.assertEqual(1, self.store.recover_proven_orphans())
        row = self.store.rows(
            """SELECT status,end_reason,duration_quality
               FROM skill_runs WHERE run_id=?""",
            (first,),
        )[0]
        self.assertEqual("interrupted", row["status"])
        self.assertEqual("proven-orphan", row["end_reason"])
        self.assertEqual("bounded", row["duration_quality"])

    def test_hook_install_preserves_existing_and_is_idempotent(self):
        path = Path(self.tmp.name) / "hooks.json"
        path.write_text(
            '{"hooks":{"PostToolUse":[{"matcher":"*","hooks":[{"type":"command","command":"existing"}]}]}}',
            encoding="utf-8",
        )
        configure_hooks.install(path)
        configure_hooks.install(path)
        config = configure_hooks.load(path)
        self.assertEqual("existing", config["hooks"]["PostToolUse"][0]["hooks"][0]["command"])
        for event in configure_hooks.EVENTS:
            telemetry = [
                hook for group in config["hooks"][event] for hook in group["hooks"]
                if configure_hooks.is_ours(hook.get("command"))
            ]
            self.assertEqual(1, len(telemetry))
        telemetry_group = next(
            group for group in config["hooks"]["PostToolUse"]
            if any(configure_hooks.is_ours(h.get("command")) for h in group["hooks"])
        )
        self.assertEqual("*", telemetry_group["matcher"])

    def test_evidence_is_idempotent_linked_and_privacy_safe(self):
        run = self.store.start_from_path(self.skill / "SKILL.md", self.event)
        canary = "PRIVATE-PROMPT-CANARY-7f0f command --secret output-body"
        first = self.store.add_evidence(
            self.event, "test", "passed", canary, idempotency_hint="call-1"
        )
        second = self.store.add_evidence(
            self.event, "test", "passed", canary, idempotency_hint="call-1"
        )
        self.assertEqual(first["evidence_id"], second["evidence_id"])
        self.assertEqual(1, first["linked_runs"])
        self.assertEqual(0, second["linked_runs"])
        link = self.store.rows("SELECT run_id,evidence_id FROM skill_run_evidence")[0]
        self.assertEqual(run, link["run_id"])
        self.assertNotIn(canary, self.store.db_path.read_bytes().decode("latin-1"))

    def test_evidence_links_latest_skill_run_across_subturns(self):
        older_event = dict(self.event, turn_id="skill-read-subturn")
        older_run = self.store.start_from_path(self.skill / "SKILL.md", older_event)
        self.store.finish_run(older_run, "returned")
        evidence_event = dict(self.event, turn_id="verification-subturn")
        result = self.store.add_evidence(
            evidence_event, "test", "passed", "opaque-test", idempotency_hint="subturn-call"
        )
        self.assertEqual(1, result["linked_runs"])
        link = self.store.rows(
            "SELECT run_id FROM skill_run_evidence WHERE evidence_id=?",
            (result["evidence_id"],),
        )[0]
        self.assertEqual(older_run, link["run_id"])

    def test_evidence_fallback_never_crosses_or_uses_empty_session(self):
        run = self.store.start_from_path(self.skill / "SKILL.md", self.event)
        other = dict(self.event, session_id="other-session", turn_id="verification")
        self.assertEqual(
            0,
            self.store.add_evidence(
                other, "test", "passed", "other", idempotency_hint="other"
            )["linked_runs"],
        )
        empty = dict(self.event, session_id="", turn_id="verification")
        self.assertEqual(
            0,
            self.store.add_evidence(
                empty, "test", "passed", "empty", idempotency_hint="empty"
            )["linked_runs"],
        )
        self.assertTrue(run)

    def test_source_plugin_identity_is_canonical(self):
        plugin_skill = (
            Path(self.tmp.name) / "plugins" / "cache" / "test-source" /
            "ai-project-manager" / "1.0.0" / "skills" /
            "project-orchestrator"
        )
        plugin_skill.mkdir(parents=True)
        manifest = plugin_skill.parents[1] / ".codex-plugin" / "plugin.json"
        manifest.parent.mkdir()
        manifest.write_text(
            json.dumps(
                {
                    "name": "ai-project-manager",
                    "version": "1.0.0",
                    "skills": "./skills/",
                }
            ),
            encoding="utf-8",
        )
        skill_file = plugin_skill / "SKILL.md"
        skill_file.write_text(
            "---\nname: spoofed-frontmatter\ndescription: demo\n---\n",
            encoding="utf-8",
        )
        with mock.patch.object(
            TelemetryStore,
            "_plugins_root",
            return_value=Path(self.tmp.name) / "plugins",
        ):
            run = self.store.start_from_path(skill_file, self.event)
        row = self.store.rows(
            "SELECT skill_key,provider,source_class FROM skill_runs WHERE run_id=?", (run,)
        )[0]
        self.assertEqual("ai-project-manager:project-orchestrator", row["skill_key"])
        self.assertEqual("ai-project-manager", row["provider"])
        self.assertEqual("plugin", row["source_class"])

    def test_plugin_identity_requires_authorized_manifest_layout(self):
        plugins_root = Path(self.tmp.name) / "plugins"
        missing_manifest = (
            plugins_root / "cache" / "test-source" / "spoof" / "1.0.0" /
            "skills" / "trusted-name"
        )
        missing_manifest.mkdir(parents=True)
        missing_file = missing_manifest / "SKILL.md"
        missing_file.write_text(
            "---\nname: skill-telemetry\ndescription: spoof\n---\n",
            encoding="utf-8",
        )
        escaping_root = (
            plugins_root / "cache" / "test-source" / "escape" / "1.0.0"
        )
        escaping_skill = escaping_root / "skills" / "trusted-name"
        escaping_skill.mkdir(parents=True)
        escaping_file = escaping_skill / "SKILL.md"
        escaping_file.write_text("name: skill-telemetry\n", encoding="utf-8")
        manifest = escaping_root / ".codex-plugin" / "plugin.json"
        manifest.parent.mkdir()
        manifest.write_text(
            json.dumps(
                {
                    "name": "escape",
                    "version": "1.0.0",
                    "skills": "../skills",
                }
            ),
            encoding="utf-8",
        )
        with mock.patch.object(
            TelemetryStore, "_plugins_root", return_value=plugins_root
        ):
            for path in (missing_file, escaping_file):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    self.store.start_from_path(path, self.event)

    def test_plugin_cache_rejects_backup_depth_and_manifest_mismatch(self):
        plugins_root = Path(self.tmp.name) / "plugins"

        def plugin_file(
            package: Path, *, name: str, version: str
        ) -> Path:
            skill = package / "skills" / "browser"
            skill.mkdir(parents=True)
            manifest = package / ".codex-plugin" / "plugin.json"
            manifest.parent.mkdir()
            manifest.write_text(
                json.dumps(
                    {
                        "name": name,
                        "version": version,
                        "skills": "./skills/",
                    }
                ),
                encoding="utf-8",
            )
            target = skill / "SKILL.md"
            target.write_text(
                "---\nname: skill-telemetry\ndescription: spoof\n---\n",
                encoding="utf-8",
            )
            return target

        backup = plugin_file(
            plugins_root / "cache" / "openai-bundled" /
            "plugin-backup-OE93r0" / "browser" / "26.1.0",
            name="browser",
            version="26.1.0",
        )
        name_mismatch = plugin_file(
            plugins_root / "cache" / "openai-bundled" /
            "browser-name" / "26.1.0",
            name="different-plugin",
            version="26.1.0",
        )
        version_mismatch = plugin_file(
            plugins_root / "cache" / "openai-bundled" /
            "browser-version" / "26.1.0",
            name="browser-version",
            version="26.2.0",
        )
        oversized = plugin_file(
            plugins_root / "cache" / "openai-bundled" /
            "browser-large" / "26.1.0",
            name="browser-large",
            version="26.1.0",
        )
        (
            oversized.parents[2] / ".codex-plugin" / "plugin.json"
        ).write_bytes(b"{" + b"x" * PLUGIN_MANIFEST_LIMIT)
        with mock.patch.object(
            TelemetryStore, "_plugins_root", return_value=plugins_root
        ):
            for path in (
                backup,
                name_mismatch,
                version_mismatch,
                oversized,
            ):
                with self.subTest(path=path):
                    started = time.monotonic()
                    self.assertEqual(
                        [],
                        self.store.skill_paths(
                            {"command": f'Get-Content -Raw "{path}"'}
                        ),
                    )
                    with self.assertRaises(ValueError):
                        self.store.start_from_path(path, self.event)
                    record = self.store.sanitize_hook_event(
                        dict(
                            self.event,
                            tool_input={
                                "command": f'Get-Content -Raw "{path}"'
                            },
                        )
                    )
                    self.assertEqual([], record["skills"])
                    self.assertRegex(record["auth_tag"], r"^[0-9a-f]{64}$")
                    self.assertLess(time.monotonic() - started, 1.5)

    def test_unregistered_spoofed_skill_cannot_enter_signed_spool(self):
        spoof = Path(self.tmp.name) / "unregistered" / "skill-telemetry"
        spoof.mkdir(parents=True)
        spoof_file = spoof / "SKILL.md"
        spoof_file.write_text(
            "---\nname: skill-telemetry\ndescription: spoof\n---\n",
            encoding="utf-8",
        )
        event = dict(
            self.event,
            tool_input={
                "command": f'Get-Content -Raw "{spoof_file}"'
            },
        )
        self.assertEqual([], self.store.skill_paths(event["tool_input"]))
        with self.assertRaises(ValueError):
            self.store.start_from_path(spoof_file, event)
        record = self.store.sanitize_hook_event(event)
        self.assertIsNotNone(record)
        self.assertEqual([], record["skills"])
        self.assertRegex(record["auth_tag"], r"^[0-9a-f]{64}$")
        self.assertIsNotNone(self.store.spool_hook_event(event))
        self.store.drain_spool()
        self.assertEqual(0, self.store.status()["counts"]["runs"])

    def test_skill_telemetry_self_read_is_recorded_once(self):
        skill_file = self.skill / "SKILL.md"
        first = self.store.start_from_path(skill_file, self.event)
        second = self.store.start_from_path(skill_file, self.event)
        self.assertTrue(first)
        self.assertEqual(first, second)
        rows = self.store.rows(
            "SELECT run_id FROM skill_runs WHERE skill_key='skill-telemetry'"
        )
        self.assertEqual(1, len(rows))

    def test_coarse_tool_evidence_classification(self):
        cases = [
            ("npm run test", {"exit_code": 0}, ("test", "passed", "test-command")),
            ("npm run build", {"exit_code": 1}, ("build", "failed", "build-command")),
            ("python validate_registry.py", {"exit_code": 0}, ("validate", "passed", "validation-command")),
            ("progress-verifier task verified", {"success": True}, ("pm-verified-task", "passed", "pm-verification")),
            ("playwright screenshot viewport", {"success": True}, ("browser-qa", "ambiguous", "browser-check")),
            ("approval required", {"is_error": True, "message": "denied"}, ("authority", "failed", "authority-boundary")),
        ]
        for command, response, expected in cases:
            event = dict(self.event, tool_name="shell", tool_input={"command": command}, tool_response=response)
            self.assertEqual(expected, self.store.classify_tool_evidence(event))
        unrelated = dict(self.event, tool_name="read_file", tool_input={"path": "README.md"}, tool_response={})
        self.assertIsNone(self.store.classify_tool_evidence(unrelated))
        ambiguous = dict(
            self.event,
            tool_name="shell",
            tool_input={"command": "npm run test"},
            tool_response={},
        )
        self.assertEqual(
            ("test", "ambiguous", "test-command"),
            self.store.classify_tool_evidence(ambiguous),
        )

    def test_schema_v5_database_migrates_to_v6_without_rekey(self):
        run = self.store.start_manual("pre-migration")
        self.store.finish_run(run, "returned")
        before = self.store.rows(
            """SELECT run_id,idempotency_key,provenance_trust
               FROM skill_runs"""
        )[0]
        db = sqlite3.connect(self.store.db_path)
        try:
            db.execute("DROP TABLE turn_lifecycle")
            db.execute(
                "UPDATE meta SET value='5' WHERE key='schema_version'"
            )
            db.execute("ALTER TABLE skill_runs DROP COLUMN end_reason")
            db.execute("ALTER TABLE skill_runs DROP COLUMN duration_quality")
            db.commit()
        finally:
            db.close()
        migrated = TelemetryStore(self.root, drain=False)
        row = migrated.rows(
            "SELECT value FROM meta WHERE key='schema_version'"
        )[0]
        self.assertEqual("6", row["value"])
        TelemetryStore(self.root, drain=False)
        tables = {
            item["name"] for item in migrated.rows(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertEqual(1, migrated.status()["counts"]["runs"])
        self.assertIn("skill_evidence", tables)
        self.assertIn("skill_run_evidence", tables)
        self.assertIn("turn_lifecycle", tables)
        after = migrated.rows(
            """SELECT run_id,idempotency_key,provenance_trust,end_reason,
                      duration_quality FROM skill_runs"""
        )[0]
        self.assertEqual(before["run_id"], after["run_id"])
        self.assertEqual(before["idempotency_key"], after["idempotency_key"])
        self.assertEqual(before["provenance_trust"], after["provenance_trust"])
        self.assertEqual("legacy-unknown", after["end_reason"])
        self.assertEqual("unknown", after["duration_quality"])

    def test_component_upgrade_preserves_v6_rows_and_legacy_seconds(self):
        run = self.store.start_manual(
            "component-upgrade", "component-session", "component-turn"
        )
        self.store.finish_run(run, "returned")
        with self.store.connection() as db:
            db.execute(
                """UPDATE skill_runs
                   SET started_at=?,ended_at=?,duration_ms=1000
                   WHERE run_id=?""",
                (
                    "2026-01-01T10:00:00+00:00",
                    "2026-01-01T10:00:01+00:00",
                    run,
                ),
            )
            db.execute(
                """UPDATE meta SET value='1.7.0'
                   WHERE key='component_version'"""
            )
        upgraded = TelemetryStore(self.root, drain=False)
        row = upgraded.rows(
            """SELECT run_id,started_at,ended_at,provenance_trust
               FROM skill_runs"""
        )[0]
        self.assertEqual(run, row["run_id"])
        self.assertEqual("2026-01-01T10:00:00+00:00", row["started_at"])
        self.assertEqual("2026-01-01T10:00:01+00:00", row["ended_at"])
        self.assertEqual("trusted", row["provenance_trust"])
        meta = {
            item["key"]: item["value"]
            for item in upgraded.rows(
                """SELECT key,value FROM meta
                   WHERE key IN ('schema_version','component_version',
                                 'privacy_repair_version')"""
            )
        }
        self.assertEqual("6", meta["schema_version"])
        self.assertEqual(COMPONENT_VERSION, meta["component_version"])
        self.assertEqual(PRIVACY_REPAIR_VERSION, meta["privacy_repair_version"])

    def test_partial_v6_missing_provenance_columns_is_repaired(self):
        run = self.store.start_manual("partial-v6")
        self.store.finish_run(run, "returned")
        db = sqlite3.connect(self.store.db_path)
        try:
            db.execute(
                "ALTER TABLE skill_runs DROP COLUMN provenance_trust"
            )
            db.execute(
                "ALTER TABLE skill_evidence DROP COLUMN provenance_trust"
            )
            db.commit()
        finally:
            db.close()
        migrated = TelemetryStore(self.root, drain=False)
        for table in ("skill_runs", "skill_evidence"):
            columns = {
                row["name"]
                for row in migrated.rows(f"PRAGMA table_info({table})")
            }
            self.assertIn("provenance_trust", columns)
        self.assertEqual(
            "legacy-unverified",
            migrated.rows(
                "SELECT provenance_trust FROM skill_runs"
            )[0]["provenance_trust"],
        )

    def test_active_reader_keeps_repair_pending_until_wal_is_scrubbed(self):
        root = Path(self.tmp.name) / "active-reader-repair"
        store = TelemetryStore(root, drain=False)
        run = store.start_manual("legacy-seed")
        store.finish_run(run, "returned")
        store.add_evaluation(
            run,
            "partial",
            {
                "outcome_achieved": 1,
                "completion_evidence": 1,
                "authority_safety": 1,
                "avoidable_rework": 1,
                "efficient_recoverable": 1,
            },
            ["test"],
            ["artifact:beforemigration123"],
            "unit-test",
        )
        canary = "ACTIVE-READER-PRIVACY-CANARY"
        shape_canary = "b" * 64
        raw_run = "skillrun_" + "b" * 32
        db = sqlite3.connect(store.db_path)
        try:
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute(
                """UPDATE skill_evaluations SET run_id=?,evidence_refs=?
                   WHERE run_id=?""",
                (
                    raw_run,
                    json.dumps([f"artifact:{canary}"]),
                    run,
                ),
            )
            db.execute(
                """UPDATE skill_runs
                   SET run_id=?,session_hash=?,turn_hash=?,repo_hash=?
                   WHERE run_id=?""",
                (
                    raw_run,
                    shape_canary,
                    shape_canary,
                    shape_canary,
                    run,
                ),
            )
            db.execute(
                """UPDATE meta SET value='1'
                   WHERE key='privacy_repair_version'"""
            )
            db.commit()
            checkpoint = db.execute(
                "PRAGMA wal_checkpoint(TRUNCATE)"
            ).fetchone()
            self.assertEqual(0, checkpoint[0])
        finally:
            db.close()

        reader = sqlite3.connect(store.db_path)
        try:
            reader.execute("BEGIN")
            self.assertEqual(
                shape_canary,
                reader.execute(
                    "SELECT session_hash FROM skill_runs"
                ).fetchone()[0],
            )
            with self.assertRaises(PrivacyRepairPendingError) as raised:
                TelemetryStore(root, drain=False)
            self.assertEqual("privacy-repair-pending", str(raised.exception))

            current = sqlite3.connect(store.db_path)
            try:
                pending_value = current.execute(
                    """SELECT value FROM meta
                       WHERE key='privacy_repair_version'"""
                ).fetchone()[0]
                first_identity = current.execute(
                    """SELECT run_id,session_hash FROM skill_runs"""
                ).fetchone()
                first_refs = current.execute(
                    "SELECT evidence_refs FROM skill_evaluations"
                ).fetchone()[0]
            finally:
                current.close()
            self.assertEqual(PRIVACY_REPAIR_PENDING, pending_value)
            self.assertNotEqual(raw_run, first_identity[0])
            self.assertNotEqual(shape_canary, first_identity[1])
            self.assertNotIn(canary, first_refs)

            for command in ("status", "doctor"):
                output = io.StringIO()
                with (
                    mock.patch.dict(
                        os.environ,
                        {"CODEX_SKILL_TELEMETRY_HOME": str(root)},
                    ),
                    mock.patch.object(
                        sys, "argv", ["telemetry_cli.py", command]
                    ),
                    redirect_stdout(output),
                ):
                    self.assertEqual(0, telemetry_cli.main())
                report = json.loads(output.getvalue())
                self.assertEqual(
                    PRIVACY_REPAIR_PENDING, report["privacy_repair"]
                )
                self.assertEqual("read-only", report["read_mode"])
            verification = sqlite3.connect(store.db_path)
            try:
                self.assertEqual(
                    PRIVACY_REPAIR_PENDING,
                    verification.execute(
                        """SELECT value FROM meta
                           WHERE key='privacy_repair_version'"""
                    ).fetchone()[0],
                )
            finally:
                verification.close()
        finally:
            reader.rollback()
            reader.close()

        finalized = TelemetryStore(root, drain=False)
        self.assertEqual(
            PRIVACY_REPAIR_VERSION,
            finalized.rows(
                """SELECT value FROM meta
                   WHERE key='privacy_repair_version'"""
            )[0]["value"],
        )
        final_identity = finalized.rows(
            "SELECT run_id,session_hash FROM skill_runs"
        )[0]
        final_refs = finalized.rows(
            "SELECT evidence_refs FROM skill_evaluations"
        )[0]["evidence_refs"]
        self.assertEqual(first_identity[0], final_identity["run_id"])
        self.assertEqual(first_identity[1], final_identity["session_hash"])
        self.assertEqual(first_refs, final_refs)

        third = TelemetryStore(root, drain=False)
        third_identity = third.rows(
            "SELECT run_id,session_hash FROM skill_runs"
        )[0]
        self.assertEqual(final_identity, third_identity)
        for candidate in root.glob("telemetry.sqlite3*"):
            raw = candidate.read_bytes().decode("latin-1")
            self.assertNotIn(canary, raw)
            self.assertNotIn(shape_canary, raw)

    def test_v2_cleanup_preserves_trusted_ids_and_provenance(self):
        root = Path(self.tmp.name) / "v2-cleanup"
        store = TelemetryStore(root, drain=False)
        run = store.start_manual(
            "trusted-skill",
            session_id="trusted-session",
            turn_id="trusted-turn",
        )
        evidence = store.add_evidence(
            {
                "session_id": "trusted-session",
                "turn_id": "trusted-turn",
                "cwd": str(self.skill),
            },
            "test",
            "passed",
            "trusted-subject",
            detection="explicit-manual",
        )
        before_run = store.rows(
            """SELECT run_id,idempotency_key,session_hash,turn_hash,
                      repo_hash,provenance_trust FROM skill_runs
               WHERE run_id=?""",
            (run,),
        )[0]
        before_evidence = store.rows(
            """SELECT evidence_id,idempotency_key,session_hash,turn_hash,
                      repo_hash,subject_hash,provenance_trust
               FROM skill_evidence WHERE evidence_id=?""",
            (evidence["evidence_id"],),
        )[0]
        db = sqlite3.connect(store.db_path)
        try:
            db.execute(
                """UPDATE meta SET value='2'
                   WHERE key='privacy_repair_version'"""
            )
            db.commit()
            self.assertEqual(
                0,
                db.execute(
                    "PRAGMA wal_checkpoint(TRUNCATE)"
                ).fetchone()[0],
            )
        finally:
            db.close()

        reader = sqlite3.connect(store.db_path)
        try:
            reader.execute("BEGIN")
            reader.execute("SELECT COUNT(*) FROM skill_runs").fetchone()
            with self.assertRaises(PrivacyRepairPendingError):
                TelemetryStore(root, drain=False)
            pending = sqlite3.connect(store.db_path)
            try:
                self.assertEqual(
                    PRIVACY_REPAIR_PENDING,
                    pending.execute(
                        """SELECT value FROM meta
                           WHERE key='privacy_repair_version'"""
                    ).fetchone()[0],
                )
                pending.row_factory = sqlite3.Row
                during_run = dict(
                    pending.execute(
                        """SELECT run_id,idempotency_key,session_hash,
                                  turn_hash,repo_hash,provenance_trust
                           FROM skill_runs WHERE run_id=?""",
                        (run,),
                    ).fetchone()
                )
                during_evidence = dict(
                    pending.execute(
                        """SELECT evidence_id,idempotency_key,session_hash,
                                  turn_hash,repo_hash,subject_hash,
                                  provenance_trust
                           FROM skill_evidence WHERE evidence_id=?""",
                        (evidence["evidence_id"],),
                    ).fetchone()
                )
            finally:
                pending.close()
            self.assertEqual(before_run, during_run)
            self.assertEqual(before_evidence, during_evidence)
        finally:
            reader.rollback()
            reader.close()

        finalized = TelemetryStore(root, drain=False)
        self.assertEqual(
            before_run,
            finalized.rows(
                """SELECT run_id,idempotency_key,session_hash,turn_hash,
                          repo_hash,provenance_trust FROM skill_runs
                   WHERE run_id=?""",
                (run,),
            )[0],
        )
        self.assertEqual(
            before_evidence,
            finalized.rows(
                """SELECT evidence_id,idempotency_key,session_hash,turn_hash,
                          repo_hash,subject_hash,provenance_trust
                   FROM skill_evidence WHERE evidence_id=?""",
                (evidence["evidence_id"],),
            )[0],
        )
        self.assertEqual("trusted", before_run["provenance_trust"])
        self.assertEqual("trusted", before_evidence["provenance_trust"])

    def test_public_write_boundaries_reject_body_like_identity_metadata(self):
        canary = "RAW PROMPT RESPONSE TOOL OUTPUT CANARY"
        with self.assertRaises(ValueError):
            self.store.start_manual(canary)
        with self.assertRaises(ValueError):
            self.store.start_manual("safe-skill", model="raw-model-body")

        run = self.store.start_manual("safe-skill", model="unknown")
        with self.assertRaises(ValueError):
            self.store.add_feedback(run, "negative", canary)
        with self.assertRaises(ValueError):
            self.store.add_evidence(
                self.event,
                "test",
                "passed",
                "transient subject body",
                detection=canary,
            )
        valid_scores = {
            "outcome_achieved": 1,
            "completion_evidence": 1,
            "authority_safety": 1,
            "avoidable_rework": 1,
            "efficient_recoverable": 1,
        }
        invalid_evaluations = [
            (["artifact"], [canary], "codex", "outcome-v1"),
            ([canary], ["artifact:opaque123"], "codex", "outcome-v1"),
            (["artifact"], ["artifact:opaque123"], canary, "outcome-v1"),
            (["artifact"], ["artifact:opaque123"], "codex", canary),
        ]
        for classes, refs, evaluator, rubric in invalid_evaluations:
            with self.subTest(
                classes=classes,
                refs=refs,
                evaluator=evaluator,
                rubric=rubric,
            ):
                with self.assertRaises(ValueError):
                    self.store.add_evaluation(
                        run,
                        "partial",
                        valid_scores,
                        classes,
                        refs,
                        evaluator,
                        rubric,
                    )
        with self.assertRaises(ValueError):
            self.store.health("collector", "error", canary)

        raw_model_event = dict(self.event, model=canary)
        model_run = self.store.start_from_path(
            self.skill / "SKILL.md", raw_model_event
        )
        model_class = self.store.rows(
            "SELECT model_class FROM skill_runs WHERE run_id=?",
            (model_run,),
        )[0]["model_class"]
        self.assertEqual("unknown", model_class)
        self.assertNotIn(
            canary, self.store.db_path.read_bytes().decode("latin-1")
        )

    def test_v6_migration_redacts_pre_privacy_free_text_and_marks_untrusted(self):
        canaries = {
            "identity": "rawpromptcanaryabc123",
            "provider": "providerpromptcanaryabc123",
            "model": "MODEL PROMPT BODY",
            "feeling": "FEELING PROMPT BODY",
            "evaluation": "EVALUATION RESPONSE BODY",
            "evidence": "EVIDENCE TOOL OUTPUT BODY",
            "health": "HEALTH TOOL OUTPUT BODY",
        }
        run = self.store.start_manual("legacy-seed")
        self.store.finish_run(run, "returned")
        canonical_skill = Path(__file__).resolve().parents[1] / "SKILL.md"
        canonical_run = self.store.start_from_path(
            canonical_skill, self.event
        )
        self.store.finish_run(canonical_run, "returned")
        preserved_run = self.store.start_from_path(
            canonical_skill,
            dict(self.event, turn_id="known-preserve"),
        )
        self.store.finish_run(preserved_run, "returned")
        self.store.add_feedback(
            run, "positive", "explicit-approval", 5
        )
        self.store.add_evidence(
            self.event,
            "test",
            "passed",
            "legacy-subject",
            detection="explicit-manual",
        )
        scores = {
            "outcome_achieved": 1,
            "completion_evidence": 1,
            "authority_safety": 1,
            "avoidable_rework": 1,
            "efficient_recoverable": 1,
        }
        self.store.add_evaluation(
            run,
            "partial",
            scores,
            ["test"],
            ["test:opaque123"],
            "codex",
        )
        self.store.health(
            "collector", "ok", "spool-v2;skills:0;evidence:0"
        )
        db = sqlite3.connect(self.store.db_path)
        try:
            db.execute(
                """UPDATE skill_runs
                   SET skill_key=?,skill_name=?,provider=?,model_class=?,
                       detection='legacy body detection'
                   WHERE run_id=?""",
                (
                    canaries["identity"],
                    canaries["identity"],
                    canaries["identity"],
                    canaries["model"],
                    run,
                ),
            )
            db.execute(
                """UPDATE skill_runs SET provider=?
                   WHERE run_id=?""",
                (canaries["provider"], canonical_run),
            )
            db.execute(
                """UPDATE skill_feedback
                   SET feeling_class=?,source='legacy body source'""",
                (canaries["feeling"],),
            )
            db.execute(
                """UPDATE skill_evaluations
                   SET rubric_version=?,evidence_classes=?,evidence_refs=?,
                       evaluator=?""",
                (
                    canaries["evaluation"],
                    json.dumps([canaries["evaluation"]]),
                    json.dumps([canaries["evaluation"]]),
                    canaries["evaluation"],
                ),
            )
            db.execute(
                "UPDATE skill_evidence SET detection=?",
                (canaries["evidence"],),
            )
            db.execute(
                """UPDATE collector_health
                   SET hook_name=?,status=?,detail_class=?""",
                (
                    canaries["health"],
                    canaries["health"],
                    canaries["health"],
                ),
            )
            db.execute(
                "UPDATE meta SET value='4' WHERE key='schema_version'"
            )
            db.execute(
                """UPDATE meta SET value='1'
                   WHERE key='privacy_repair_version'"""
            )
            db.commit()
        finally:
            db.close()

        migrated = TelemetryStore(self.root, drain=False)
        raw_database = migrated.db_path.read_bytes().decode("latin-1")
        for canary in canaries.values():
            self.assertNotIn(canary, raw_database)
        status = migrated.status()
        self.assertEqual(3, status["counts"]["runs"])
        self.assertEqual(3, status["counts"]["legacy_unverified_runs"])
        self.assertEqual(0, status["counts"]["trusted_runs"])
        self.assertEqual(
            "legacy-redacted",
            migrated.rows(
                "SELECT feeling_class FROM skill_feedback"
            )[0]["feeling_class"],
        )
        preserved = migrated.rows(
            """SELECT skill_key,skill_name,provider,source_class
               FROM skill_runs
               WHERE skill_key='skill-telemetry' AND provider='local'""",
        )[0]
        self.assertEqual(
            {
                "skill_key": "skill-telemetry",
                "skill_name": "skill-telemetry",
                "provider": "local",
                "source_class": "custom",
            },
            preserved,
        )

    def test_legacy_repair_removes_canary_from_every_text_column(self):
        canary = "LEGACYTEXTCANARYZ9"
        shape_canary = "a" * 64
        root = Path(self.tmp.name) / "all-text-migration"
        store = TelemetryStore(root, drain=False)
        event = {
            "session_id": "legacy-session",
            "turn_id": "legacy-turn",
            "cwd": str(self.skill),
        }
        run = store.start_manual(
            "legacy-seed",
            session_id=event["session_id"],
            turn_id=event["turn_id"],
            cwd=event["cwd"],
        )
        store.finish_run(run, "returned")
        store.add_feedback(run, "positive", "explicit-approval", 5)
        evidence = store.add_evidence(
            event,
            "test",
            "passed",
            "legacy-subject",
            detection="explicit-manual",
        )
        evidence_id = evidence["evidence_id"]
        store.add_evaluation(
            run,
            "partial",
            {
                "outcome_achieved": 1,
                "completion_evidence": 1,
                "authority_safety": 1,
                "avoidable_rework": 1,
                "efficient_recoverable": 1,
            },
            ["test"],
            ["artifact:legacyopaque123"],
            "unit-test",
        )
        store.health("collector", "ok", "spool-v2;skills:0;evidence:0")

        raw_run = "skillrun_" + shape_canary[:32]
        raw_evidence = "skillevidence_" + shape_canary[:32]
        db = sqlite3.connect(store.db_path)
        try:
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute(
                """UPDATE skill_runs
                   SET run_id=?,idempotency_key=?,skill_key=?,skill_name=?,
                       provider=?,source_class=?,skill_fingerprint=?,
                       session_hash=?,turn_hash=?,repo_hash=?,model_class=?,
                       detection=?,status=?,started_at=?,ended_at=?,
                       provenance_trust=? WHERE run_id=?""",
                (
                    raw_run,
                    shape_canary,
                    f"{canary}-skill-key",
                    f"{canary}-skill-name",
                    f"{canary}-provider",
                    f"{canary}-source",
                    shape_canary,
                    shape_canary,
                    shape_canary,
                    shape_canary,
                    f"{canary}-model",
                    f"{canary}-detection",
                    f"{canary}-status",
                    f"{canary}-started",
                    f"{canary}-ended",
                    f"{canary}-provenance",
                    run,
                ),
            )
            db.execute(
                """UPDATE skill_feedback
                   SET feedback_id=?,run_id=?,sentiment=?,feeling_class=?,
                       source=?,reaction_signature=?,created_at=?""",
                (
                    "skillfb_" + shape_canary[:32],
                    raw_run,
                    f"{canary}-sentiment",
                    f"{canary}-feeling",
                    f"{canary}-feedback-source",
                    shape_canary,
                    f"{canary}-feedback-time",
                ),
            )
            db.execute(
                """UPDATE skill_evaluations
                   SET evaluation_id=?,run_id=?,skill_fingerprint=?,
                       rubric_version=?,outcome=?,evidence_classes=?,
                       evidence_refs=?,evaluator=?,reviewed_at=?""",
                (
                    "skilleval_" + shape_canary[:32],
                    raw_run,
                    shape_canary,
                    f"{canary}-rubric",
                    f"{canary}-outcome",
                    json.dumps([f"{canary}-class"]),
                    json.dumps([f"artifact:{shape_canary}"]),
                    f"{canary}-evaluator",
                    f"{canary}-reviewed",
                ),
            )
            db.execute(
                """UPDATE skill_evidence
                   SET evidence_id=?,idempotency_key=?,session_hash=?,
                       turn_hash=?,repo_hash=?,evidence_class=?,result=?,
                       subject_hash=?,detection=?,observed_at=?,
                       provenance_trust=? WHERE evidence_id=?""",
                (
                    raw_evidence,
                    shape_canary,
                    shape_canary,
                    shape_canary,
                    shape_canary,
                    f"{canary}-evidence-class",
                    f"{canary}-evidence-result",
                    shape_canary,
                    f"{canary}-evidence-detection",
                    f"{canary}-evidence-time",
                    f"{canary}-evidence-provenance",
                    evidence_id,
                ),
            )
            db.execute(
                """UPDATE skill_run_evidence
                   SET run_id=?,evidence_id=?,linked_at=?""",
                (raw_run, raw_evidence, f"{canary}-linked"),
            )
            db.execute(
                """UPDATE collector_health
                   SET observed_at=?,hook_name=?,status=?,detail_class=?""",
                (
                    f"{canary}-health-time",
                    f"{canary}-hook",
                    f"{canary}-health-status",
                    f"{canary}-health-detail",
                ),
            )
            db.execute(
                "INSERT INTO spool_receipts(event_id,processed_at) VALUES(?,?)",
                (shape_canary, f"{canary}-processed"),
            )
            db.execute(
                """INSERT INTO turn_lifecycle(
                     session_hash,turn_hash,prompt_started_at,stopped_at
                   ) VALUES(?,?,?,?)""",
                (
                    shape_canary,
                    shape_canary,
                    f"{canary}-prompt-time",
                    f"{canary}-stop-time",
                ),
            )
            db.execute(
                """INSERT INTO meta(key,value) VALUES(?,?)""",
                (f"{canary}-meta-key", f"{canary}-meta-value"),
            )
            db.execute(
                """INSERT OR REPLACE INTO meta(key,value)
                   VALUES('outcome_v2_cycle_start',?)""",
                (f"{canary}-cycle-time",),
            )
            db.execute(
                """UPDATE meta SET value=?
                   WHERE key IN (
                     'spool_schema_version','component_version',
                     'privacy_repair_version'
                   )""",
                (shape_canary,),
            )
            db.execute(
                "UPDATE meta SET value='4' WHERE key='schema_version'"
            )
            db.commit()
        finally:
            db.close()

        migrated = TelemetryStore(root, drain=False)
        tables = [
            row["name"]
            for row in migrated.rows(
                """SELECT name FROM sqlite_master
                   WHERE type='table' AND name NOT LIKE 'sqlite_%'"""
            )
        ]
        for table in tables:
            text_columns = [
                row["name"]
                for row in migrated.rows(f"PRAGMA table_info({table})")
                if "TEXT" in str(row["type"]).upper()
            ]
            if not text_columns:
                continue
            for row in migrated.rows(
                f"SELECT {','.join(text_columns)} FROM {table}"
            ):
                for column in text_columns:
                    with self.subTest(table=table, column=column):
                        self.assertNotIn(canary, str(row[column]))
                        self.assertNotIn(shape_canary, str(row[column]))
        for candidate in root.glob("telemetry.sqlite3*"):
            raw = candidate.read_bytes().decode("latin-1")
            self.assertNotIn(canary, raw)
            self.assertNotIn(shape_canary, raw)

        repaired_run = migrated.rows(
            """SELECT status,provenance_trust,session_hash FROM skill_runs"""
        )[0]
        self.assertEqual("interrupted", repaired_run["status"])
        self.assertEqual("legacy-unverified", repaired_run["provenance_trust"])
        repaired_evaluation = migrated.rows(
            """SELECT outcome,outcome_achieved,completion_evidence,
                      authority_safety,avoidable_rework,
                      efficient_recoverable,evidence_classes,evidence_refs
               FROM skill_evaluations"""
        )[0]
        self.assertEqual("unverified", repaired_evaluation["outcome"])
        for score in (
            "outcome_achieved",
            "completion_evidence",
            "authority_safety",
            "avoidable_rework",
            "efficient_recoverable",
        ):
            self.assertIsNone(repaired_evaluation[score])
        self.assertEqual(
            ["legacy-redacted"],
            json.loads(repaired_evaluation["evidence_classes"]),
        )
        self.assertRegex(
            json.loads(repaired_evaluation["evidence_refs"])[0],
            r"^artifact:[0-9a-f]{64}$",
        )
        repaired_evidence = migrated.rows(
            "SELECT evidence_class,result,session_hash FROM skill_evidence"
        )[0]
        self.assertEqual("legacy-redacted", repaired_evidence["evidence_class"])
        self.assertEqual("ambiguous", repaired_evidence["result"])
        lifecycle_session = migrated.rows(
            "SELECT session_hash FROM turn_lifecycle"
        )[0]["session_hash"]
        self.assertEqual(
            repaired_run["session_hash"], repaired_evidence["session_hash"]
        )
        self.assertEqual(repaired_run["session_hash"], lifecycle_session)

    def test_capture_hook_failure_is_fail_open_and_stores_no_body(self):
        hook = Path(__file__).with_name("capture_hook.py")
        hook_root = Path(self.tmp.name) / "privacy-hook-state"
        TelemetryStore(hook_root, drain=False)
        env = dict(os.environ, CODEX_SKILL_TELEMETRY_HOME=str(hook_root))
        canary = "HOOK-PRIVATE-CANARY-e317 prompt response output"
        event = dict(
            self.event,
            hook_event_name="PostToolUse",
            tool_name="shell",
            tool_use_id="privacy-call",
            tool_input={"command": f"npm run test {canary}"},
            tool_response={"exit_code": 1, "stderr": canary},
        )
        result = subprocess.run(
            [sys.executable, "-X", "utf8", str(hook)],
            input=json.dumps(event),
            text=True,
            capture_output=True,
            env=env,
            timeout=5,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        hook_store = TelemetryStore(hook_root, drain=False)
        hook_store.drain_spool()
        self.assertEqual(1, hook_store.status()["counts"]["evidence"])
        self.assertNotIn(canary, hook_store.db_path.read_bytes().decode("latin-1"))

    def test_capture_hook_end_to_end(self):
        hook = Path(__file__).with_name("capture_hook.py")
        hook_root = Path(self.tmp.name) / "hook-state"
        TelemetryStore(hook_root, drain=False)
        env = dict(os.environ, CODEX_SKILL_TELEMETRY_HOME=str(hook_root))
        post = dict(self.event, hook_event_name="PostToolUse", tool_response={"exit_code": 0})
        stop = dict(self.event, hook_event_name="Stop")
        reaction = dict(self.event, hook_event_name="UserPromptSubmit", turn_id="t2", prompt="ナイス！")
        for event in (post, stop, reaction):
            result = subprocess.run(
                [sys.executable, "-X", "utf8", str(hook)],
                input=json.dumps(event, ensure_ascii=False),
                text=True,
                encoding="utf-8",
                capture_output=True,
                env=env,
                timeout=5,
            )
            self.assertEqual(0, result.returncode, result.stderr)
        hook_store = TelemetryStore(hook_root, drain=False)
        hook_store.drain_spool()
        status = hook_store.status()
        self.assertEqual(1, status["counts"]["runs"])
        self.assertEqual(1, status["counts"]["returned"])
        self.assertEqual(1, status["counts"]["feedback"])


if __name__ == "__main__":
    unittest.main()
