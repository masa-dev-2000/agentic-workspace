from __future__ import annotations

import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest import mock

import evaluate_sampled_outcomes
import outcome_v2
from outcome_v2 import (
    candidate_runs,
    classify,
    cycle_start,
    evidence_for_run,
    registry_contracts,
    start_cycle,
)
from telemetry_store import TelemetryStore
from evaluate_sampled_outcomes import load_rollouts


class OutcomeV2Tests(unittest.TestCase):
    def test_cycle_start_is_immutable(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        self.assertEqual("2026-07-28T00:00:00+00:00", start_cycle(db, "2026-07-28T00:00:00+00:00"))
        self.assertEqual("2026-07-28T00:00:00+00:00", start_cycle(db, "2027-01-01T00:00:00+00:00"))
        self.assertEqual("2026-07-28T00:00:00+00:00", cycle_start(db))

    def test_cycle_start_rejects_free_text_timestamp(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        canary = "RAW PROMPT BODY AS TIMESTAMP"
        with self.assertRaises(ValueError):
            start_cycle(db, canary)
        self.assertEqual(0, db.execute("SELECT COUNT(*) FROM meta").fetchone()[0])

    def test_only_linked_structured_evidence_is_read(self):
        db = sqlite3.connect(":memory:")
        db.execute(
            """CREATE TABLE skill_evidence(
               evidence_id TEXT,evidence_class TEXT,result TEXT,
               subject_hash TEXT,provenance_trust TEXT)"""
        )
        db.execute("CREATE TABLE skill_run_evidence(run_id TEXT,evidence_id TEXT)")
        db.execute(
            "INSERT INTO skill_evidence VALUES('e1','test','passed','opaque','trusted')"
        )
        db.execute(
            """INSERT INTO skill_evidence
               VALUES('e2','test','passed','opaque','legacy-unverified')"""
        )
        db.execute("INSERT INTO skill_run_evidence VALUES('r1','e1')")
        db.execute("INSERT INTO skill_run_evidence VALUES('r1','e2')")
        self.assertEqual("e1", evidence_for_run(db, "r1")[0]["evidence_id"])
        self.assertEqual(1, len(evidence_for_run(db, "r1")))
        self.assertEqual([], evidence_for_run(db, "r2"))

    def test_conservative_classification(self):
        evidence = [{"evidence_id": "e1", "evidence_class": "test", "result": "passed"}]
        outcome, scores, _, _ = classify("returned", 0, evidence, {"test"})
        self.assertEqual("partial", outcome)
        self.assertEqual(1, scores["outcome_achieved"])
        positive = evidence + [
            {"evidence_id": "e2", "evidence_class": "explicit-feedback", "result": "passed"}
        ]
        without_authority = classify(
            "returned", 0, positive, {"test", "explicit-feedback"}
        )
        self.assertEqual("partial", without_authority[0])
        self.assertEqual(1, without_authority[1]["authority_safety"])
        authority = positive + [
            {
                "evidence_id": "e3",
                "evidence_class": "authority",
                "result": "passed",
            }
        ]
        still_partial = classify(
            "returned",
            0,
            authority,
            {"test", "explicit-feedback", "authority"},
        )
        self.assertEqual("partial", still_partial[0])
        domain_verified = evidence + authority[-1:] + [
            {
                "evidence_id": "e4",
                "evidence_class": "domain-verdict",
                "result": "passed",
                "detection": "explicit-manual",
            }
        ]
        verified = classify(
            "returned",
            0,
            domain_verified,
            {"test", "explicit-feedback", "authority"},
        )
        self.assertEqual("verified-success", verified[0])
        self.assertEqual(1, verified[1]["avoidable_rework"])
        self.assertEqual(1, verified[1]["efficient_recoverable"])

    def test_authority_failure_is_rejected(self):
        evidence = [{"evidence_id": "e1", "evidence_class": "authority", "result": "failed"}]
        self.assertEqual("rejected", classify("returned", 0, evidence, {"authority"})[0])

    def test_interrupted_authority_only_has_no_completion_score(self):
        evidence = [
            {
                "evidence_id": "e1",
                "evidence_class": "authority",
                "result": "passed",
            }
        ]
        outcome, scores, _, _ = classify(
            "interrupted", 0, evidence, {"authority"}
        )
        self.assertEqual("rework-required", outcome)
        self.assertEqual(0, scores["completion_evidence"])

    def test_generated_partial_is_accepted_by_store_contract(self):
        evidence = [
            {
                "evidence_id": "skillevidence_" + "0" * 32,
                "evidence_class": "test",
                "result": "passed",
            }
        ]
        outcome, scores, classes, refs = classify(
            "returned", 0, evidence, {"test"}
        )
        self.assertEqual("partial", outcome)
        self.assertEqual(1, scores["efficient_recoverable"])
        with tempfile.TemporaryDirectory() as directory:
            store = TelemetryStore(Path(directory) / "telemetry")
            run = store.start_manual("safe-skill")
            store.finish_run(run, "returned")
            evaluation = store.add_evaluation(
                run,
                outcome,
                scores,
                classes,
                refs,
                "unit-test",
                "outcome-v2",
            )
            self.assertTrue(evaluation.startswith("skilleval_"))

    def test_sampled_partial_respects_shared_score_contract(self):
        segment = [
            {
                "payload": {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "shell",
                    "arguments": "python -m unittest",
                }
            },
            {
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call-1",
                    "output": "tests passed; exit code 0",
                }
            },
            {"payload": {"type": "task_complete"}},
        ]
        outcome, scores, classes, _ = (
            evaluate_sampled_outcomes.classify_segment(
                mock.Mock(), segment, {"feedback_sentiment": None}
            )
        )
        self.assertEqual("partial", outcome)
        self.assertEqual(1, scores["efficient_recoverable"])
        TelemetryStore.validate_evaluation_contract(
            outcome, scores, classes, ["evidence:opaque123"]
        )

    def test_v1_table_is_not_touched_by_helpers(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        db.execute("CREATE TABLE skill_evaluations(rubric_version TEXT)")
        db.execute("INSERT INTO skill_evaluations VALUES('outcome-v1')")
        start_cycle(db, "2026-07-28T00:00:00+00:00")
        self.assertEqual(1, db.execute(
            "SELECT COUNT(*) FROM skill_evaluations WHERE rubric_version='outcome-v1'"
        ).fetchone()[0])

    def test_candidate_set_is_bounded_to_ten_per_skill(self):
        db = sqlite3.connect(":memory:")
        db.execute("""CREATE TABLE skill_runs(
            run_id TEXT,skill_key TEXT,started_at TEXT,status TEXT,
            tool_failure_count INTEGER,provenance_trust TEXT)""")
        db.execute("CREATE TABLE skill_evaluations(run_id TEXT,rubric_version TEXT,outcome TEXT)")
        for index in range(12):
            db.execute(
                "INSERT INTO skill_runs VALUES(?,?,?,?,0,'trusted')",
                (
                    f"r{index}",
                    "failure-loop-guard",
                    f"2026-07-28T10:{index:02d}:00+00:00",
                    "returned",
                ),
            )
        db.execute("INSERT INTO skill_evaluations VALUES('r0','outcome-v1','unverified')")
        rows = candidate_runs(db, "2026-07-28T09:50:50+00:00")
        self.assertEqual(10, len(rows))
        self.assertIn("r0", {row["run_id"] for row in rows})

    def test_default_registry_completion_evidence_contracts_load(self):
        path = Path(__file__).resolve().parents[2] / "skill-registry.yaml"
        contracts = registry_contracts(path)
        self.assertEqual(
            {
                "test",
                "validate",
                "artifact",
                "explicit-feedback",
                "authority",
            },
            contracts["failure-loop-guard"],
        )
        self.assertEqual(
            {
                "test",
                "build",
                "validate",
                "artifact",
                "domain-verdict",
                "explicit-feedback",
                "authority",
            },
            contracts["skill-telemetry"],
        )
        self.assertEqual(
            {
                "pm-verified-task",
                "artifact",
                "validate",
                "explicit-feedback",
                "authority",
            },
            contracts["ai-project-manager:project-orchestrator"],
        )

    def test_registry_contracts_fail_closed_for_missing_or_invalid_classes(self):
        for accepted in (None, ["prompt-body"]):
            with self.subTest(accepted=accepted):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "registry.yaml"
                    lines = ["skills:"]
                    for key in (
                        "failure-loop-guard",
                        "skill-telemetry",
                        "ai-project-manager:project-orchestrator",
                    ):
                        lines.extend(
                            [
                                f"  - key: {key}",
                                "    completion:",
                                "      proof: [opaque]",
                            ]
                        )
                        if accepted is not None:
                            lines.append(
                                "      acceptedEvidence: "
                                + str(accepted).replace("'", "")
                            )
                    path.write_text("\n".join(lines), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        registry_contracts(path)

    def test_missing_rollout_state_is_read_only_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            missing_state = base / "missing" / "state.sqlite"
            store = TelemetryStore(base / "telemetry", initialize=False)
            self.assertEqual({}, load_rollouts(store, missing_state))
            self.assertFalse(missing_state.exists())
            self.assertFalse((base / "telemetry").exists())

    def test_unavailable_rollout_state_is_not_modified(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            state = base / "state.sqlite"
            original = b"not-a-sqlite-database"
            state.write_bytes(original)
            store = TelemetryStore(base / "telemetry", initialize=False)
            self.assertEqual({}, load_rollouts(store, state))
            self.assertEqual(original, state.read_bytes())
            self.assertFalse((base / "telemetry").exists())

    def test_outcome_status_constructs_read_only_store(self):
        database = sqlite3.connect(":memory:")
        database.execute(
            "CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)"
        )

        @contextmanager
        def read_connection():
            yield database

        fake = mock.Mock()
        fake.read_connection = read_connection
        output = io.StringIO()
        registry = Path(__file__).resolve().parents[2] / "skill-registry.yaml"
        with (
            mock.patch.object(
                outcome_v2, "TelemetryStore", return_value=fake
            ) as constructor,
            mock.patch.object(
                sys,
                "argv",
                [
                    "outcome_v2.py",
                    "status",
                    "--registry",
                    str(registry),
                ],
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(2, outcome_v2.main())
        constructor.assert_called_once_with(initialize=False)
        fake.connection.assert_not_called()
        database.close()

    def test_sampled_report_constructs_read_only_store(self):
        fake = mock.Mock()
        fake.rows.return_value = []
        fake.evaluation_sample.return_value = []
        output = io.StringIO()
        with (
            mock.patch.object(
                evaluate_sampled_outcomes,
                "TelemetryStore",
                return_value=fake,
            ) as constructor,
            mock.patch.object(
                evaluate_sampled_outcomes,
                "load_rollouts",
                return_value={},
            ),
            mock.patch.object(
                sys, "argv", ["evaluate_sampled_outcomes.py"]
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(0, evaluate_sampled_outcomes.main())
        constructor.assert_called_once_with(initialize=False)
        self.assertEqual([], json.loads(output.getvalue()))


if __name__ == "__main__":
    unittest.main()
