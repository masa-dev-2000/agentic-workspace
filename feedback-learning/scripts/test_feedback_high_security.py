from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from feedback_store import (
    PURGE_CONFIRMATION,
    FeedbackStore,
    InvalidHmacKeyError,
    InvalidStateMarkerError,
    PurgedStateError,
    UnsafePurgeTargetError,
    nonblocking_process_lock,
    sanitize_text,
)


CLI = Path(__file__).with_name("feedback_cli.py")
HOOK = Path(__file__).with_name("capture_hook.py")


class FeedbackHighSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, root: Path, *args: str) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["CODEX_FEEDBACK_LEARNING_HOME"] = str(root)
        return subprocess.run(
            [sys.executable, "-X", "utf8", str(CLI), *args],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )

    def hook(self, root: Path) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["CODEX_FEEDBACK_LEARNING_HOME"] = str(root)
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "ダメじゃん。同じ失敗を繰り返さず登録して。",
            "session_id": "security-session",
            "turn_id": "security-turn",
            "cwd": r"C:\repo",
        }
        return subprocess.run(
            [sys.executable, "-X", "utf8", str(HOOK)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )

    def eligible_proposal(self, root: Path):
        store = FeedbackStore(root)
        common = {
            "feedback_type": "request",
            "subject_class": "verification",
            "theme_key": "verification-proof",
            "impact": "medium",
            "explicitness": "explicit",
            "capture_mode": "manual",
            "desired_template": "Require trusted experiment verification evidence",
        }
        store.add_feedback(
            common | {"session_id": "security-s1", "turn_id": "security-t1"}
        )
        store.add_feedback(
            common | {"session_id": "security-s2", "turn_id": "security-t2"}
        )
        pattern = store.build_patterns()[0]
        target_hashes = {"feedback-learning": "a" * 64}
        proposal = store.create_proposal(
            pattern["pattern_id"],
            requested_surface="existing-skill",
            target_ids=["feedback-learning"],
            target_hashes=target_hashes,
        )
        return store, pattern, proposal, target_hashes

    def start_eligible_experiment(self, root: Path):
        store, pattern, proposal, target_hashes = self.eligible_proposal(root)
        approval = store.record_approval(
            proposal["proposal_id"],
            target_hashes,
            datetime.now(timezone.utc) + timedelta(hours=1),
            "security-test-approval",
        )
        experiment = store.start_experiment(
            proposal["proposal_id"],
            approval["approval_token"],
            target_hashes,
            "Trusted evidence should be required.",
        )
        return store, pattern, proposal, experiment

    def test_corrupt_hmac_key_fails_closed_without_rotation_and_status_explains(self):
        root = self.base / "invalid-key"
        store = FeedbackStore(root)
        store.initialize()
        db_before = store.db_path.read_bytes()
        store.key_path.write_text('{"password":"NOT_A_KEY"}', encoding="ascii")

        with self.assertRaises(InvalidHmacKeyError):
            store.initialize()
        self.assertEqual('{"password":"NOT_A_KEY"}', store.key_path.read_text())
        self.assertEqual(db_before, store.db_path.read_bytes())

        status = store.status()
        self.assertEqual("error", status["health"])
        self.assertEqual("invalid-hmac-key", status["error_class"])
        self.assertIn("64-lowercase-hex", status["reason"])
        self.assertFalse(status["key_valid"])

        for command in ("status", "doctor"):
            result = self.cli(root, command)
            self.assertEqual(4, result.returncode)
            payload = json.loads(result.stdout)
            self.assertEqual("invalid-hmac-key", payload["error_class"])
        self.assertEqual('{"password":"NOT_A_KEY"}', store.key_path.read_text())

    def test_short_hex_key_is_rejected_as_not_32_bytes(self):
        root = self.base / "short-key"
        store = FeedbackStore(root)
        store.initialize()
        store.key_path.write_text("00" * 16, encoding="ascii")
        with self.assertRaises(InvalidHmacKeyError):
            store.digest("value")

    def test_purge_requires_owned_temp_authority_and_prevents_hook_recreation(self):
        root = self.base / "purge-authorized"
        store = FeedbackStore(root, test_purge_authority=True)
        store.initialize()
        with self.assertRaises(ValueError):
            store.purge("wrong-confirmation")
        self.assertTrue(root.exists())

        result = store.purge(PURGE_CONFIRMATION)
        self.assertFalse(root.exists())
        self.assertTrue(Path(result["tombstone"]).is_file())
        self.assertFalse(result["residual"])

        hook = self.hook(root)
        self.assertEqual(0, hook.returncode)
        self.assertFalse(root.exists())
        self.assertFalse((root / "spool").exists())
        with self.assertRaises(PurgedStateError):
            FeedbackStore(root).initialize()

    def test_purge_refuses_arbitrary_env_directory_and_preserves_contents(self):
        root = self.base / "arbitrary" / "feedback-learning"
        store = FeedbackStore(root)
        store.initialize()
        sentinel = root / "DO-NOT-DELETE.txt"
        sentinel.write_text("preserve", encoding="utf-8")
        result = self.cli(
            root,
            "purge",
            "--confirm",
            PURGE_CONFIRMATION,
        )
        self.assertEqual(4, result.returncode)
        payload = json.loads(result.stdout)
        self.assertEqual("unsafe-purge-target", payload["error_class"])
        self.assertEqual("preserve", sentinel.read_text(encoding="utf-8"))
        self.assertFalse(store.tombstone_path.exists())

    def test_purge_revalidates_marker_and_disables_before_busy_lock(self):
        tampered_root = self.base / "tampered-marker"
        tampered = FeedbackStore(
            tampered_root,
            test_purge_authority=True,
        )
        tampered.initialize()
        tampered.state_marker_path.write_text("{}", encoding="utf-8")
        with self.assertRaises(InvalidStateMarkerError):
            tampered.purge(PURGE_CONFIRMATION)
        self.assertTrue(tampered_root.exists())

        busy_root = self.base / "busy-purge"
        busy = FeedbackStore(busy_root, test_purge_authority=True)
        busy.initialize()
        with nonblocking_process_lock(busy.drain_lock_path) as acquired:
            self.assertTrue(acquired)
            with self.assertRaises(UnsafePurgeTargetError):
                busy.purge(PURGE_CONFIRMATION)
        self.assertTrue((busy_root / "disabled").is_file())
        self.assertTrue(busy_root.exists())
        self.assertFalse(busy.tombstone_path.exists())

    def test_invented_or_legacy_verification_ref_cannot_validate_pattern(self):
        root = self.base / "verification"
        store, pattern, _, experiment = self.start_eligible_experiment(root)
        experiment_id = experiment["experiment_id"]
        with self.assertRaises(ValueError):
            store.evaluate_experiment(
                experiment_id,
                outcome="improved",
                verification="verified",
                evidence_refs=["artifact:invented"],
            )
        self.assertEqual(
            "running",
            store.rows(
                "SELECT status FROM experiments WHERE experiment_id=?",
                (experiment_id,),
            )[0]["status"],
        )

        with store.connect() as db:
            db.execute(
                """INSERT INTO verification_evidence(
                     evidence_id,experiment_id,evidence_ref,outcome,
                     verification,evidence_class,recorded_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    "vev_legacy",
                    experiment_id,
                    "artifact:legacy",
                    "improved",
                    "verified",
                    "artifact",
                    "2026-07-31T00:00:00+00:00",
                ),
            )
        with self.assertRaises(ValueError):
            store.evaluate_experiment(
                experiment_id,
                outcome="improved",
                verification="verified",
                evidence_refs=["artifact:legacy"],
            )

        with self.assertRaises(ValueError):
            store.record_verification_evidence(
                experiment_id,
                evidence_ref="artifact:wrong-class",
                outcome="improved",
                verification="verified",
                evidence_class="human-confirmation",
            )
        evidence = store.record_verification_evidence(
            experiment_id,
            evidence_ref="artifact:verified-build",
            outcome="improved",
            verification="verified",
            evidence_class="artifact",
        )
        self.assertEqual("trusted", evidence["provenance_trust"])
        evaluation = store.evaluate_experiment(
            experiment_id,
            outcome="improved",
            verification="verified",
            evidence_refs=["artifact:verified-build"],
        )
        self.assertEqual("validated", evaluation["pattern_status"])
        current = store.rows(
            "SELECT status FROM improvement_patterns WHERE pattern_id=?",
            (pattern["pattern_id"],),
        )[0]
        self.assertEqual("validated", current["status"])

    def test_verification_outcome_and_level_must_match_ledger_evidence(self):
        root = self.base / "verification-match"
        store, _, _, experiment = self.start_eligible_experiment(root)
        experiment_id = experiment["experiment_id"]
        store.record_verification_evidence(
            experiment_id,
            evidence_ref="artifact:unchanged",
            outcome="unchanged",
            verification="verified",
            evidence_class="artifact",
        )
        with self.assertRaises(ValueError):
            store.evaluate_experiment(
                experiment_id,
                outcome="improved",
                verification="verified",
                evidence_refs=["artifact:unchanged"],
            )

    def test_verify_record_cli_creates_trusted_bound_evidence(self):
        root = self.base / "verification-cli"
        store, _, _, experiment = self.start_eligible_experiment(root)
        result = self.cli(
            root,
            "verify-record",
            experiment["experiment_id"],
            "--evidence-ref",
            "external:release-check",
            "--outcome",
            "improved",
            "--verification",
            "verified",
            "--evidence-class",
            "external-state",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual("trusted", payload["provenance_trust"])
        self.assertEqual(experiment["experiment_id"], payload["experiment_id"])
        row = store.rows(
            """SELECT * FROM verification_evidence
               WHERE evidence_id=?""",
            (payload["evidence_id"],),
        )[0]
        self.assertEqual("external-state", row["evidence_class"])

    def test_staging_artifacts_are_verified_at_approval_and_experiment_start(self):
        root = self.base / "staging-integrity"
        store, _, proposal, target_hashes = self.eligible_proposal(root)
        staging = Path(proposal["staging_path"])
        proposal_path = staging / "proposal.json.disabled"
        changeset_path = staging / "changeset.json.disabled"
        original_proposal = proposal_path.read_text(encoding="utf-8")
        proposal_json = json.loads(original_proposal)
        proposal_json["title"] = "tampered"
        proposal_path.write_text(
            json.dumps(proposal_json),
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            store.record_approval(
                proposal["proposal_id"],
                target_hashes,
                datetime.now(timezone.utc) + timedelta(hours=1),
                "tampered-proposal",
            )

        proposal_path.write_text(original_proposal, encoding="utf-8")
        approval = store.record_approval(
            proposal["proposal_id"],
            target_hashes,
            datetime.now(timezone.utc) + timedelta(hours=1),
            "valid-proposal",
        )
        changeset_json = json.loads(changeset_path.read_text(encoding="utf-8"))
        changeset_json["operations"]["change_summary"] = "tampered"
        changeset_path.write_text(
            json.dumps(changeset_json),
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            store.start_experiment(
                proposal["proposal_id"],
                approval["approval_token"],
                target_hashes,
                "Tampered staging must not consume approval.",
            )
        approval_row = store.rows(
            "SELECT status,used_at FROM approvals WHERE approval_id=?",
            (approval["approval_id"],),
        )[0]
        self.assertEqual("recorded", approval_row["status"])
        self.assertIsNone(approval_row["used_at"])

    def test_staging_path_must_remain_inside_exact_proposal_directory(self):
        root = self.base / "staging-path"
        store, _, proposal, target_hashes = self.eligible_proposal(root)
        elsewhere = self.base / "elsewhere"
        elsewhere.mkdir()
        with store.connect() as db:
            db.execute(
                """UPDATE improvement_proposals SET staging_path=?
                   WHERE proposal_id=?""",
                (str(elsewhere), proposal["proposal_id"]),
            )
        with self.assertRaises(ValueError):
            store.record_approval(
                proposal["proposal_id"],
                target_hashes,
                datetime.now(timezone.utc) + timedelta(hours=1),
                "escaped-staging-path",
            )

    def test_json_quoted_secret_is_redacted_before_manual_persistence(self):
        root = self.base / "json-secret"
        store = FeedbackStore(root)
        canary = "CANARY_SHORT_SECRET"
        source = (
            '{"password":"'
            + canary
            + '","nested":{"access_token":"OTHER_CANARY"},"note":"keep"}'
        )
        sanitized = sanitize_text(source)
        self.assertNotIn(canary, sanitized)
        self.assertNotIn("OTHER_CANARY", sanitized)
        self.assertIn("[REDACTED]", sanitized)
        feedback_id, inserted = store.add_feedback(
            {
                "feedback_type": "request",
                "subject_class": "privacy",
                "capture_mode": "manual",
                "desired_template": source,
            }
        )
        self.assertTrue(inserted)
        row = store.rows(
            """SELECT expectation_template,observed_template,desired_template,
                      event_json
               FROM feedback_events WHERE feedback_id=?""",
            (feedback_id,),
        )[0]
        persisted = json.dumps(row, ensure_ascii=False)
        self.assertNotIn(canary, persisted)
        self.assertNotIn("OTHER_CANARY", persisted)
        self.assertIn("[REDACTED]", row["desired_template"])


if __name__ == "__main__":
    unittest.main()
