from __future__ import annotations

import importlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import capture_hook
import failure_store


class FailureLearningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.previous = os.environ.get("CODEX_FAILURE_LEARNING_HOME")
        os.environ["CODEX_FAILURE_LEARNING_HOME"] = self.temp.name
        importlib.reload(failure_store)
        importlib.reload(capture_hook)

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("CODEX_FAILURE_LEARNING_HOME", None)
        else:
            os.environ["CODEX_FAILURE_LEARNING_HOME"] = self.previous
        self.temp.cleanup()

    def payload(self) -> dict:
        return {
            "session_id": "session-secret",
            "turn_id": "turn-1",
            "cwd": r"C:\\Users\\alice\\private-repo",
            "hook_event_name": "PostToolUse",
            "permission_mode": "default",
            "tool_name": "Bash",
            "tool_use_id": "call-1",
            "tool_input": {"command": "curl -H 'Authorization: Bearer topsecret' https://example.test"},
            "tool_response": {"success": False, "exit_code": 5, "stderr": "Access denied for alice@example.com token=abcdefghijklmnopqrstuvwxyz123456"},
        }

    def signed(self, envelope: dict) -> dict:
        key = failure_store.provision_identity_key().read_bytes()
        return failure_store.authenticate_spool_envelope(envelope, key)

    def insert_verified(self, event: dict) -> bool:
        return failure_store.process_spool_envelope(self.signed(event)) == "inserted"

    def test_failure_is_sanitized_and_inserted(self) -> None:
        event = capture_hook.build_event(self.payload())
        self.assertIsNotNone(event)
        serialized = json.dumps(event)
        self.assertNotIn("alice@example.com", serialized)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz123456", serialized)
        self.assertNotIn("topsecret", serialized)
        self.assertNotIn("private-repo", serialized)
        self.assertTrue(self.insert_verified(event))
        self.assertFalse(self.insert_verified(event))

    def test_success_is_not_recorded(self) -> None:
        payload = self.payload()
        payload["tool_response"] = {"success": True, "exit_code": 0, "output": "ok"}
        self.assertIsNone(capture_hook.build_event(payload))

    def test_http_success_code_is_not_a_failure(self) -> None:
        payload = self.payload()
        payload["tool_response"] = {"status": "ok", "code": 200, "output": "ok"}
        self.assertIsNone(capture_hook.build_event(payload))

    def test_error_words_inside_successful_content_are_not_failures(self) -> None:
        payload = self.payload()
        payload["tool_name"] = "webrun"
        payload["tool_response"] = {
            "status": "ok",
            "content": [{"type": "text", "text": "An article mentions timeout and permission denied."}],
        }
        self.assertIsNone(capture_hook.build_event(payload))

    def test_nested_agent_timeout_is_not_a_tool_failure(self) -> None:
        payload = self.payload()
        payload["tool_name"] = "collaborationlist_agents"
        payload["tool_response"] = {
            "agents": [{"name": "reviewer", "status": "timeout"}],
        }
        self.assertIsNone(capture_hook.build_event(payload))

    def test_only_authenticated_later_success_records_recovery(self) -> None:
        payload = self.payload()
        event = capture_hook.build_event(payload)
        self.insert_verified(event)
        payload["tool_response"] = {"success": True, "exit_code": 0, "output": "ok"}
        self.assertFalse(failure_store.record_recovery(payload))
        with failure_store.connect_readonly() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM recovery_markers").fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM intervention_outcomes").fetchone()[0],
                0,
            )
        recovery = capture_hook.build_recovery_envelope(payload)
        self.assertEqual(
            failure_store.process_spool_envelope(self.signed(recovery)),
            "recovery-recorded",
        )
        with failure_store.connect() as conn:
            row = conn.execute(
                "SELECT status, verification, causal_strength FROM intervention_outcomes"
            ).fetchone()
        self.assertEqual(dict(row), {
            "status": "success",
            "verification": "indirect",
            "causal_strength": "none",
        })

    def test_shell_exit_zero_is_an_explicit_success(self) -> None:
        self.assertTrue(capture_hook.success_signal("Exit code: 0\nOutput:\nok"))
        self.assertFalse(capture_hook.success_signal("Exit code: 1\nOutput:\nfailed"))

    def test_patterns_are_rebuildable(self) -> None:
        event = capture_hook.build_event(self.payload())
        self.insert_verified(event)
        self.assertEqual(failure_store.rebuild_patterns(), 1)
        with failure_store.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM patterns").fetchone()[0], 1)

    def test_concurrent_duplicate_writes_are_idempotent(self) -> None:
        event = capture_hook.build_event(self.payload())
        errors = []

        def worker() -> None:
            try:
                failure_store.process_spool_envelope(self.signed(event))
            except sqlite3.Error as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertFalse(errors)
        with failure_store.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)

    def test_hook_always_exits_zero(self) -> None:
        env = os.environ.copy()
        env["CODEX_FAILURE_LEARNING_HOME"] = self.temp.name
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "capture_hook.py")],
            input=json.dumps(self.payload()), text=True, capture_output=True, env=env, timeout=5,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
