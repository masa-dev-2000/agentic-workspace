import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import task_optimizer as optimizer


class OptimizerContractTests(unittest.TestCase):
    def target(self):
        contract = optimizer._registry_fingerprint("skill-telemetry")
        return {"skill_key": "skill-telemetry", **contract}

    def candidate(self):
        target = self.target()
        return {
            "schema_version": "candidate_v2",
            "candidate_id": "test-candidate",
            "source_report_generated_at": "2026-08-05T00:00:00Z",
            "source_report_digest": "sha256:test-report",
            "target": target,
            "evidence": {"evidence_refs": ["local:test/evidence"], "sample_size": 20},
            "before_metrics": {"tasks": 20, "verified_success_task_rate": 0.2},
            "proposal": {"concrete_scope": "skill-telemetry: bounded recovery check", "expected_delta": {"failure_rate": "decrease"}},
            "impact": {"rollback_ref": "local:test/rollback"},
            "validation_plan": {"fixture_refs": ["local:test/fixture"], "eval_ids": ["before-after"], "thresholds": {"failure_rate": "< baseline"}, "nonregression": ["existing-tests"], "min_samples": 20},
            "approval_required": True,
            "status": "candidate-awaiting-approval",
        }

    def test_rejects_zero_verified_success(self):
        candidate = self.candidate()
        candidate["before_metrics"]["verified_success_task_rate"] = 0
        self.assertEqual(optimizer.validate_candidate(candidate), (False, "missing-verified-success"))

    def test_lifecycle_and_fingerprint_guard(self):
        candidate = self.candidate()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.json"
            path.write_text(json.dumps(candidate), encoding="utf-8")
            target = self.target()
            optimizer.approve_candidate(str(path), "test-candidate", target, "local:approval/1", "local:actor/test")
            optimizer.apply_candidate(str(path), "test-candidate", target, "local:approval/1", "local:changeset/1", target["contractContentDigest"])
            result = optimizer.rollback_candidate(str(path), "test-candidate", target, "local:test/rollback", "local:actor/test")
            self.assertEqual(result["status"], "rolled_back")

    def test_apply_rejects_different_approval_ref(self):
        candidate = self.candidate()
        candidate["status"] = "approved"
        candidate["approval"] = {"approval_ref": "local:approval/1"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.json"
            path.write_text(json.dumps(candidate), encoding="utf-8")
            with self.assertRaises(ValueError):
                optimizer.apply_candidate(str(path), "test-candidate", self.target(), "local:approval/other", "local:changeset/1", self.target()["contractContentDigest"])

    def test_legacy_migration_is_non_destructive_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "optimization" / "candidates"
            source_dir.mkdir(parents=True)
            reports_dir = root / "optimization" / "reports"
            reports_dir.mkdir(parents=True)
            report_body = {"generated_at": "2026-08-05T00:00:00Z", "trusted_task_count": 20}
            report_body["report_digest"] = optimizer._report_digest(report_body)
            (reports_dir / "report.json").write_text(json.dumps(report_body), encoding="utf-8")
            db = sqlite3.connect(root / "telemetry.sqlite3")
            db.execute("create table skill_evidence (evidence_id text primary key, provenance_trust text)")
            db.execute("insert into skill_evidence values ('e1', 'trusted')")
            db.commit()
            db.close()
            legacy = {
                "schema_version": 1,
                "skill": "skill-telemetry",
                "generated_at": "2026-08-05T00:00:00Z",
                "source_report_digest": report_body["report_digest"],
                "evidence": {"evidence_refs": ["local:evidence/e1"]},
                "metrics": {"tasks": 20, "verified_success_task_rate": 0.2},
                "proposal": {"problem": "tool failures", "concrete_scope": "bounded recovery check", "expected_delta": {"failure_rate": "decrease"}},
                "impact": {"rollback_ref": "local:rollback/legacy"},
                "validation_plan": {"fixture_refs": ["local:fixture/e1"], "eval_ids": ["before-after"], "min_samples": 20, "thresholds": {"failure_rate": "< baseline"}, "nonregression": ["existing-tests"]},
            }
            source = source_dir / "legacy.json"
            source.write_text(json.dumps(legacy), encoding="utf-8")
            first = optimizer.migrate_legacy_candidates(root, write=True)
            second = optimizer.migrate_legacy_candidates(root, write=True)
            self.assertEqual(len(first["converted"]), 1)
            self.assertEqual(len(second["converted"]), 0)
            self.assertTrue(source.exists())

    def test_legacy_missing_trusted_report_is_measurement_gap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "optimization" / "candidates"
            source_dir.mkdir(parents=True)
            source = source_dir / "legacy.json"
            source.write_text(json.dumps({"schema_version": 1, "skill": "skill-telemetry", "source_report_digest": "sha256:not-found", "metrics": {"tasks": 20, "verified_success_task_rate": 0.2}}), encoding="utf-8")
            result = optimizer.migrate_legacy_candidates(root, write=True)
            self.assertEqual(result["converted"], [])
            self.assertEqual(result["measurement_gap"], ["legacy.json"])

    def test_migration_rejects_network_root(self):
        with self.assertRaises(ValueError):
            optimizer.migrate_legacy_candidates("\\\\server\\share")

    def test_legacy_schema_is_rejected(self):
        candidate = self.candidate()
        candidate["schema_version"] = 2
        self.assertEqual(optimizer.validate_candidate(candidate), (False, "legacy-schema"))


if __name__ == "__main__":
    unittest.main()
