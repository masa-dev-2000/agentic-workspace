from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("health_cli.py")


class HealthCliTests(unittest.TestCase):
    def run_cli(self, root: Path, *args: str) -> tuple[subprocess.CompletedProcess, dict]:
        p = subprocess.run([sys.executable, str(SCRIPT), *args, "--root", str(root)],
                           text=True, encoding="utf-8", capture_output=True, check=False)
        return p, json.loads(p.stdout)

    def write_event(self, root: Path, ledger: str, name: str, value: dict) -> None:
        target = root / ledger / "spool"
        target.mkdir(parents=True, exist_ok=True)
        (target / name).write_text(json.dumps(value), encoding="utf-8")

    def test_dry_run_is_body_free_and_detects_duplicate_orphan(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            event = {"event_id": "a" * 64, "observed_at": "2026-08-04T00:00:00+00:00", "session_hash": "b" * 64}
            self.write_event(root, "skill-telemetry", "a.json", event)
            self.write_event(root, "failure-learning", "b.json", {"event_id": event["event_id"], "observed_at": event["observed_at"]})
            process, result = self.run_cli(root, "dry-run-drain", "--budget", "1")
            self.assertEqual(process.returncode, 0)
            self.assertEqual(result["status"], "dry-run")
            self.assertEqual(result["duplicates"], 1)
            self.assertEqual(result["orphans"], 1)
            self.assertTrue(result["body_free"])
            self.assertNotIn(event["event_id"], (root / "skill-telemetry" / "health-ledger.jsonl").read_text())
            self.assertIn('"operation":"dry-run"', (root / "skill-telemetry" / "health-ledger.jsonl").read_text())

    def test_malformed_and_forbidden_body_are_degraded(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.write_event(root, "feedback-learning", "bad.json", {"event_id": "x", "prompt": "secret"})
            process, result = self.run_cli(root, "health")
            self.assertEqual(process.returncode, 0)
            self.assertEqual(result["state"], "DEGRADED")
            self.assertNotIn("secret", process.stdout)

    def test_sequence_gap_is_degraded(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.write_event(root, "skill-telemetry", "a.json", {
                "event_id": "a" * 64, "observed_at": "2026-08-04T00:00:00+00:00", "session_hash": "b" * 64, "sequence": 1})
            self.write_event(root, "skill-telemetry", "c.json", {
                "event_id": "c" * 64, "observed_at": "2026-08-04T00:00:01+00:00", "session_hash": "b" * 64, "sequence": 3})
            process, result = self.run_cli(root, "health")
            self.assertEqual(process.returncode, 0)
            self.assertEqual(result["state"], "DEGRADED")
            self.assertEqual(result["gaps"], 1)

    def test_stale_and_no_data_states(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _, empty = self.run_cli(root, "health")
            self.assertEqual(empty["state"], "INCOMPLETE")
            self.write_event(root, "skill-telemetry", "old.json", {
                "event_id": "d" * 64, "observed_at": "2000-01-01T00:00:00+00:00", "session_hash": "b" * 64})
            _, stale = self.run_cli(root, "health", "--hook-observed", "--episodic-observed")
            self.assertEqual(stale["state"], "STALE")

    def test_lock_busy_and_budget(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            lock = root / "skill-telemetry" / ".health.lock"
            lock.parent.mkdir(parents=True)
            lock.write_text("busy", encoding="utf-8")
            process, result = self.run_cli(root, "dry-run-drain")
            self.assertEqual(process.returncode, 2)
            self.assertEqual(result["state"], "BLOCKED")
            process, result = self.run_cli(root, "health")
            self.assertEqual(process.returncode, 2)
            self.assertEqual(result["state"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
