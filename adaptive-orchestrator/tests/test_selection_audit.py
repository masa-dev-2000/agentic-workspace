import sqlite3
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
runner_spec = importlib.util.spec_from_file_location("stage_runner", ROOT / "scripts" / "stage_runner.py")
stage_runner = importlib.util.module_from_spec(runner_spec)
sys.modules["stage_runner"] = stage_runner
runner_spec.loader.exec_module(stage_runner)
audit_spec = importlib.util.spec_from_file_location("selection_audit", ROOT / "scripts" / "selection_audit.py")
selection_audit = importlib.util.module_from_spec(audit_spec)
audit_spec.loader.exec_module(selection_audit)


def payload(**changes):
    value = {
        "job_id": "job-1", "session_hash": "a" * 64, "turn_hash": "b" * 64,
        "registry_revision": "registry-1", "taxonomy_version": "taxonomy-1",
        "observation_state": "complete", "observation_window_closed": True,
        "telemetry_health": "complete",
        "candidates": [{"skill_key": "build-complete-app", "sources": ["registry_profile", "planner_candidate"]}],
        "observed": [],
        "comparisons": {"build-complete-app": {"eligible_runs": 5, "eligible_sessions": 3, "success_rate_bp": 9000, "baseline_success_rate_bp": 9000, "duration_reduction_bp": 2500, "metric_source": "per_turn_runtime", "comparison_quality": "eligible", "uncertainty": "pass", "retry_policy": "excluded", "wait_policy": "excluded"}},
    }
    value.update(changes)
    return value


class SelectionAuditTests(unittest.TestCase):
    def test_high_confidence_requires_complete_evidence(self):
        self.assertEqual(selection_audit.audit(payload())["candidates"][0]["classification"], "missed_candidate")

    def test_observation_failure_has_precedence(self):
        self.assertEqual(selection_audit.audit(payload(observation_state="incomplete"))["candidates"][0]["classification"], "not_observable")

    def test_planner_only_is_not_high_confidence(self):
        value = payload(candidates=[{"skill_key": "build-complete-app", "sources": ["planner_candidate"]}])
        self.assertEqual(selection_audit.audit(value)["candidates"][0]["classification"], "candidate_signal")

    def test_selected_beats_missing_observation(self):
        self.assertEqual(selection_audit.audit(payload(observed=["build-complete-app"], observation_state="incomplete"))["candidates"][0]["classification"], "selected")

    def test_persistence_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "orchestration.sqlite3"
            connection = stage_runner.connect(db)
            try:
                stage_runner.migrate(connection)
                stage_runner.create_job(connection, {"job_id": "job-1", "session_hash": "a" * 64, "turn_hash": "b" * 64, "cwd_hash": "c" * 64, "prompt_hash": "d" * 64})
            finally:
                connection.close()
            selection_audit.audit(payload(), str(db))
            selection_audit.audit(payload(), str(db))
            connection = sqlite3.connect(db)
            try:
                self.assertEqual(connection.execute("select count(*) from ao_selection_audits").fetchone()[0], 1)
                self.assertEqual(connection.execute("select count(*) from ao_selection_candidates").fetchone()[0], 1)
            finally:
                connection.close()
