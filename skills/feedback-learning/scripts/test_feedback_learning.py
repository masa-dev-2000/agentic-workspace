from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from feedback_store import FeedbackStore, sanitize_text
from configure_hook import install as install_hook, installed as hook_installed, uninstall as uninstall_hook


class FeedbackLearningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "feedback-learning"
        self.store = FeedbackStore(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def record(self, **extra):
        data = {"feedback_type": "request", "subject_class": "review", "impact": "medium", "explicitness": "explicit", "capture_mode": "manual", "desired_template": "レビュー指示を短くしたい", "session_id": "s1", "turn_id": "t1"}
        data.update(extra)
        return self.store.add_feedback(data)

    def test_sanitizes_secrets_and_paths(self):
        value = sanitize_text("token=abcd1234 C:\\Users\\masa\\repo https://x.test/a?q=secret user@example.com")
        self.assertNotIn("abcd1234", value)
        self.assertNotIn("masa", value)
        self.assertNotIn("q=secret", value)
        self.assertNotIn("user@example.com", value)

    def test_deduplicates_and_is_immutable(self):
        first, inserted = self.record()
        second, duplicate = self.record()
        self.assertTrue(inserted)
        self.assertFalse(duplicate)
        self.assertEqual(first, second)
        with self.store.connect() as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE feedback_events SET impact='high' WHERE feedback_id=?", (first,))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM feedback_events WHERE feedback_id=?", (first,))

    def test_rebuild_keeps_feedback_evidence(self):
        self.record()
        self.record(turn_id="t2", session_id="s2", desired_template="GANレビューを一発で起動したい")
        self.assertEqual(1, self.store.rebuild())
        theme = self.store.rows("SELECT * FROM themes")[0]
        self.assertEqual(2, theme["incident_count"])
        self.assertEqual(2, theme["independent_sessions"])
        self.assertEqual(2, self.store.status()["feedback_events"])

    def test_third_party_metadata_is_pseudonymous_and_raw_defaults_none(self):
        feedback_id, inserted = self.record(
            source_kind="third-party",
            speaker_id="customer@example.com",
            channel="meeting",
            subject_kind="project",
            valence="negative",
            privacy_class="restricted",
            consent_basis="user-provided",
            directness="reported",
            reliability="medium",
        )
        self.assertTrue(inserted)
        row = self.store.rows("SELECT * FROM feedback_events WHERE feedback_id=?", (feedback_id,))[0]
        self.assertEqual("third-party", row["source_kind"])
        self.assertNotEqual("", row["speaker_hash"])
        self.assertNotIn("customer", row["speaker_hash"])
        self.assertEqual("", row["raw_ref"])
        with self.assertRaises(ValueError):
            self.record(source_kind="third-party", raw_text="verbatim third-party feedback")

    def test_signal_pattern_eligibility_requires_persistence_or_two_recent_sessions(self):
        self.record(impact="high", session_id="only-session", turn_id="high-1")
        patterns = self.store.build_patterns()
        self.assertEqual("review-eligible", patterns[0]["eligibility"])
        self.assertNotEqual("validated", patterns[0]["status"])

        self.record(session_id="second-session", turn_id="high-2")
        patterns = self.store.build_patterns()
        self.assertEqual("proposal-eligible", patterns[0]["eligibility"])

        other = FeedbackStore(Path(self.tmp.name) / "persistent-feedback")
        other.add_feedback({
            "feedback_type": "request",
            "subject_class": "workflow",
            "impact": "medium",
            "explicitness": "explicit",
            "capture_mode": "manual",
            "desired_template": "この改善を恒久化してほしい",
            "session_id": "single",
            "turn_id": "single-turn",
            "persistence_requested": True,
        })
        pattern = other.build_patterns()[0]
        self.assertEqual("proposal-eligible", pattern["eligibility"])
        self.assertNotEqual("validated", pattern["status"])

        windowed = FeedbackStore(Path(self.tmp.name) / "windowed-feedback")
        base = {
            "feedback_type": "request",
            "subject_class": "window",
            "impact": "medium",
            "explicitness": "explicit",
            "capture_mode": "manual",
            "desired_template": "Repeat only when evidence is recent",
        }
        windowed.add_feedback(base | {
            "session_id": "old",
            "turn_id": "old",
            "observed_at": datetime.now(timezone.utc) - timedelta(days=100),
        })
        windowed.add_feedback(base | {"session_id": "recent", "turn_id": "recent"})
        self.assertEqual("observed", windowed.build_patterns()[0]["eligibility"])

    def test_patterns_preserve_support_counter_and_boundary_refs(self):
        self.record(session_id="support-1", turn_id="support-1")
        self.record(session_id="support-2", turn_id="support-2")
        self.record(session_id="counter", turn_id="counter", evidence_role="counter")
        self.record(session_id="boundary", turn_id="boundary", evidence_role="boundary")
        pattern = self.store.build_patterns()[0]
        self.assertEqual(2, len(json.loads(pattern["support_refs_json"])))
        self.assertEqual(1, len(json.loads(pattern["counter_refs_json"])))
        self.assertEqual(1, len(json.loads(pattern["boundary_refs_json"])))
        self.assertEqual("review-required", pattern["status"])

    def test_old_database_migrates_idempotently(self):
        self.root.mkdir(parents=True)
        legacy = sqlite3.connect(self.root / "feedback.sqlite3")
        legacy.execute("""CREATE TABLE feedback_events(
            feedback_id TEXT PRIMARY KEY, observed_at TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
            signature TEXT NOT NULL, session_hash TEXT NOT NULL, turn_hash TEXT NOT NULL, repo_hash TEXT NOT NULL,
            feedback_type TEXT NOT NULL, subject_class TEXT NOT NULL, theme_key TEXT NOT NULL, impact TEXT NOT NULL,
            explicitness TEXT NOT NULL, capture_mode TEXT NOT NULL, expectation_template TEXT NOT NULL,
            observed_template TEXT NOT NULL, desired_template TEXT NOT NULL, event_json TEXT NOT NULL, created_at TEXT NOT NULL)""")
        legacy.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        legacy.execute("INSERT INTO meta VALUES('schema_version','1')")
        legacy.commit()
        legacy.close()
        self.store.initialize()
        self.store.initialize()
        with self.store.connect() as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(feedback_events)")}
            approval_columns = {row[1] for row in db.execute("PRAGMA table_info(approvals)")}
            version = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
            privacy_version = db.execute(
                "SELECT value FROM meta WHERE key='privacy_repair_version'"
            ).fetchone()[0]
        self.assertIn("source_kind", columns)
        self.assertIn("raw_ref", columns)
        self.assertIn("provenance_trust", columns)
        self.assertIn("changeset_hash", approval_columns)
        self.assertEqual("4", version)
        self.assertEqual("3", privacy_version)

    def _eligible_preparation_pattern(self):
        common = {
            "feedback_type": "request",
            "subject_class": "preparation",
            "theme_key": "preparation-readiness",
            "impact": "medium",
            "explicitness": "explicit",
            "capture_mode": "manual",
            "desired_template": "説明資料にdraft review finalの期限を置き、人へ依頼し、催促して証拠を検証してほしい",
        }
        self.store.add_feedback(common | {"session_id": "prep-1", "turn_id": "prep-turn-1"})
        self.store.add_feedback(common | {"session_id": "prep-2", "turn_id": "prep-turn-2"})
        return self.store.build_patterns()[0]

    def test_preparation_routes_to_existing_skill_edges_not_new_skill(self):
        pattern = self._eligible_preparation_pattern()
        targets = {
            "project.plan": "1" * 64,
            "human.request": "2" * 64,
            "task.remind": "3" * 64,
            "task.verify": "4" * 64,
        }
        proposal = self.store.create_proposal(
            pattern["pattern_id"],
            requested_surface="auto",
            target_hashes=targets,
            change_summary="Connect planning, human request, reminder, and verification.",
        )
        self.assertEqual("skill-edge", proposal["surface"])
        self.assertEqual(set(targets), set(proposal["target_hashes"]))
        self.assertEqual("disabled-staged", proposal["status"])
        self.assertTrue(Path(proposal["staging_path"], "proposal.json.disabled").exists())
        self.assertFalse(Path(proposal["staging_path"], "SKILL.md").exists())

    def test_new_skill_surface_requires_no_owner_and_open_gate(self):
        pattern = self._eligible_preparation_pattern()
        gate_root = Path(self.tmp.name) / "governance"
        gate_root.mkdir()
        (gate_root / "skill-maturity-gate.json").write_text('{"status":"open"}', encoding="utf-8")
        open_gate_store = FeedbackStore(self.root, governance_root=gate_root)
        with self.assertRaises(ValueError):
            open_gate_store.create_proposal(
                pattern["pattern_id"],
                requested_surface="new-skill",
                capability_owner="project-orchestrator",
                target_hashes={"new-skill": "a" * 64},
            )
        with self.assertRaises(ValueError):
            self.store.create_proposal(
                pattern["pattern_id"],
                requested_surface="new-skill",
                capability_owner="",
                target_hashes={"new-skill": "a" * 64},
            )
        proposal = open_gate_store.create_proposal(
            pattern["pattern_id"],
            requested_surface="new-skill",
            capability_owner="",
            target_hashes={"new-skill": "a" * 64},
        )
        self.assertEqual("disabled-staged", proposal["status"])

    def test_approval_rejects_stale_hash_and_is_single_use(self):
        pattern = self._eligible_preparation_pattern()
        targets = {"feedback-learning": "a" * 64}
        proposal = self.store.create_proposal(
            pattern["pattern_id"],
            requested_surface="existing-skill",
            target_ids=list(targets),
            target_hashes=targets,
            change_summary="Improve the existing feedback workflow.",
        )
        original_operations = self.store.rows(
            "SELECT operations_json FROM change_sets WHERE proposal_id=?", (proposal["proposal_id"],)
        )[0]["operations_json"]
        approval = self.store.record_approval(
            proposal["proposal_id"],
            targets,
            datetime.now(timezone.utc) + timedelta(hours=1),
            approval_ref="current-user-explicit-approval",
        )
        self.assertTrue(approval["approval_token"].startswith("apt_"))
        with self.store.connect() as db:
            db.execute(
                "UPDATE change_sets SET target_hashes_json=? WHERE proposal_id=?",
                (json.dumps({"feedback-learning": "b" * 64}), proposal["proposal_id"]),
            )
        with self.assertRaises(ValueError):
            self.store.start_experiment(
                proposal["proposal_id"],
                approval["approval_token"],
                targets,
                "The change reduces avoidable rework.",
            )
        with self.store.connect() as db:
            db.execute(
                "UPDATE change_sets SET target_hashes_json=? WHERE proposal_id=?",
                (json.dumps(targets), proposal["proposal_id"]),
            )
            db.execute(
                "UPDATE change_sets SET operations_json=? WHERE proposal_id=?",
                (json.dumps({"apply": True}), proposal["proposal_id"]),
            )
        with self.assertRaises(ValueError):
            self.store.start_experiment(
                proposal["proposal_id"],
                approval["approval_token"],
                targets,
                "Changed operations must invalidate approval.",
            )
        with self.store.connect() as db:
            db.execute(
                "UPDATE change_sets SET operations_json=? WHERE proposal_id=?",
                (original_operations, proposal["proposal_id"]),
            )
        experiment = self.store.start_experiment(
            proposal["proposal_id"],
            approval["approval_token"],
            targets,
            "The change reduces avoidable rework.",
        )
        self.assertEqual("running", experiment["status"])
        with self.assertRaises(ValueError):
            self.store.start_experiment(
                proposal["proposal_id"],
                approval["approval_token"],
                targets,
                "Token reuse must fail.",
            )

    def test_expired_approval_is_rejected(self):
        pattern = self._eligible_preparation_pattern()
        targets = {"feedback-learning": "c" * 64}
        proposal = self.store.create_proposal(
            pattern["pattern_id"],
            requested_surface="existing-skill",
            target_ids=list(targets),
            target_hashes=targets,
        )
        approval = self.store.record_approval(
            proposal["proposal_id"],
            targets,
            datetime.now(timezone.utc) + timedelta(hours=1),
            approval_ref="expired-test",
        )
        with self.store.connect() as db:
            db.execute(
                "UPDATE approvals SET expires_at=? WHERE approval_id=?",
                ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), approval["approval_id"]),
            )
        with self.assertRaises(ValueError):
            self.store.start_experiment(
                proposal["proposal_id"],
                approval["approval_token"],
                targets,
                "Expired approval must fail.",
            )

    def test_evaluation_records_outcome_without_claiming_causality(self):
        self.record(
            subject_class="single-persistent",
            theme_key="single-persistent",
            session_id="single",
            turn_id="single",
            persistence_requested=True,
        )
        pattern = self.store.build_patterns()[0]
        targets = {"feedback-learning": "d" * 64}
        proposal = self.store.create_proposal(
            pattern["pattern_id"],
            requested_surface="existing-skill",
            target_ids=list(targets),
            target_hashes=targets,
        )
        approval = self.store.record_approval(
            proposal["proposal_id"],
            targets,
            datetime.now(timezone.utc) + timedelta(hours=1),
            approval_ref="single-evidence-test",
        )
        experiment = self.store.start_experiment(
            proposal["proposal_id"],
            approval["approval_token"],
            targets,
            "Test a bounded existing-Skill adjustment.",
        )
        evaluation = self.store.evaluate_experiment(
            experiment["experiment_id"],
            outcome="improved",
            verification="observed",
            evidence_refs=["test:feedback-learning"],
            notes="Observed improvement does not establish sole causality.",
        )
        self.assertEqual("not-established", evaluation["causal_claim"])
        current = self.store.rows("SELECT status FROM improvement_patterns WHERE pattern_id=?", (pattern["pattern_id"],))[0]
        self.assertNotEqual("validated", current["status"])

    def test_disable_prevents_capture(self):
        self.store.set_enabled(False)
        feedback_id, inserted = self.record()
        self.assertIsNone(feedback_id)
        self.assertFalse(inserted)

    def test_hook_is_narrow_and_fail_open(self):
        env = os.environ.copy()
        env["CODEX_FEEDBACK_LEARNING_HOME"] = str(self.root)
        self.store.initialize()
        hook = Path(__file__).with_name("capture_hook.py")
        payload = {"hook_event_name": "UserPromptSubmit", "prompt": "毎回レビューの指示を出すのが面倒。仕組みが欲しい", "session_id": "s", "turn_id": "t", "cwd": "C:\\repo"}
        # ASCII JSON avoids the parent process' Windows pipe encoding; the hook
        # itself runs in UTF-8 mode, matching the installed lifecycle command.
        run = subprocess.run([sys.executable, "-X", "utf8", str(hook)], input=json.dumps(payload), text=True, capture_output=True, env=env)
        self.assertEqual(0, run.returncode)
        self.assertEqual("", run.stdout)
        self.assertEqual(0, FeedbackStore(self.root).status()["feedback_events"])
        self.assertEqual(1, FeedbackStore(self.root).status()["pending_spool"])
        self.assertEqual(1, FeedbackStore(self.root).drain_spool()["processed"])
        self.assertEqual(1, FeedbackStore(self.root).status()["feedback_events"])
        payload["prompt"] = "今日の天気を教えて"
        payload["turn_id"] = "t2"
        subprocess.run([sys.executable, "-X", "utf8", str(hook)], input=json.dumps(payload), text=True, capture_output=True, env=env, check=True)
        self.assertEqual(1, FeedbackStore(self.root).status()["feedback_events"])
        self.assertEqual(0, FeedbackStore(self.root).status()["pending_spool"])

    def test_hook_configuration_preserves_existing_entries(self):
        config = {"description": "existing", "hooks": {"PostToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "existing.py"}]}]}}
        self.assertTrue(install_hook(config, Path(__file__).parents[1]))
        self.assertTrue(hook_installed(config))
        self.assertIn("PostToolUse", config["hooks"])
        self.assertFalse(install_hook(config, Path(__file__).parents[1]))
        self.assertTrue(uninstall_hook(config))
        self.assertFalse(hook_installed(config))
        self.assertIn("PostToolUse", config["hooks"])

    def test_cli_exposes_staged_evolution_without_publish(self):
        cli = Path(__file__).with_name("feedback_cli.py")
        run = subprocess.run(
            [sys.executable, "-X", "utf8", str(cli), "--help"],
            text=True,
            capture_output=True,
            check=True,
        )
        for command in ("drain", "signals", "patterns", "propose", "approve-record", "experiment", "evaluate"):
            self.assertIn(command, run.stdout)
        self.assertNotIn("publish", run.stdout)
        self.assertNotIn("draft-skill", run.stdout)

    def test_cli_runs_staged_evolution_end_to_end(self):
        cli = Path(__file__).with_name("feedback_cli.py")
        env = os.environ.copy()
        env["CODEX_FEEDBACK_LEARNING_HOME"] = str(self.root)

        def call(*args):
            run = subprocess.run(
                [sys.executable, "-X", "utf8", str(cli), *args],
                text=True,
                capture_output=True,
                env=env,
                check=True,
            )
            return json.loads(run.stdout)

        common = (
            "add", "--type", "request", "--subject", "preparation",
            "--theme-key", "preparation-readiness",
            "--desired", "draft review final の期限と依頼、催促、検証を接続してほしい",
        )
        call(*common, "--session", "cli-s1", "--turn", "cli-t1")
        call(*common, "--session", "cli-s2", "--turn", "cli-t2")
        pattern = call("patterns")[0]
        hashes = {
            "project.plan": "1" * 64,
            "human.request": "2" * 64,
            "task.remind": "3" * 64,
            "task.verify": "4" * 64,
        }
        hash_args = tuple(part for item in hashes.items() for part in ("--target-hash", f"{item[0]}={item[1]}"))
        proposal = call("propose", pattern["pattern_id"], *hash_args)
        approval = call(
            "approve-record", proposal["proposal_id"], *hash_args,
            "--approval-ref", "cli-explicit-approval",
        )
        experiment = call(
            "experiment", proposal["proposal_id"],
            "--approval-token", approval["approval_token"],
            *hash_args,
            "--hypothesis", "A bounded edge change reduces preparation gaps.",
        )
        evaluation = call(
            "evaluate", experiment["experiment_id"],
            "--outcome", "improved",
            "--verification", "observed",
            "--evidence-ref", "test:cli-e2e",
        )
        self.assertEqual("not-established", evaluation["causal_claim"])


if __name__ == "__main__":
    unittest.main()
