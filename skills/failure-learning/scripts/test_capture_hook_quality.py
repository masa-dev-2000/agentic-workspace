from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import capture_hook
import failure_store


class CaptureHookQualityTests(unittest.TestCase):
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
            "session_id": "session-1",
            "turn_id": "turn-1",
            "tool_use_id": "call-1",
            "cwd": r"C:\Users\alice\repo-a",
            "hook_event_name": "PostToolUse",
            "permission_mode": "default",
            "tool_name": "mcp__node_repl__js",
            "tool_input": {"code": "do_work()"},
            "tool_response": {
                "success": False,
                "stderr": "ModuleNotFoundError: No module named 'yaml'",
            },
        }

    def test_structured_failure_uses_diagnostic_fields_not_generic_text(self) -> None:
        payload = self.payload()
        payload["tool_response"] = {
            "success": False,
            "message": "worker failed",
            "exception": {
                "type": "ModuleNotFoundError",
                "message": "No module named 'yaml'",
            },
        }
        event = capture_hook.build_event(payload)
        self.assertIsNotNone(event)
        self.assertNotEqual(event["message_template"], "structured tool failure")
        self.assertIn("ModuleNotFoundError", event["message_template"])
        self.assertTrue(event["error_identity"].startswith("module:not_found:"))

    def test_exception_field_is_an_explicit_failure_without_success_flag(self) -> None:
        payload = self.payload()
        payload["tool_response"] = {
            "exception": {
                "type": "ModuleNotFoundError",
                "message": "No module named 'yaml'",
            }
        }
        event = capture_hook.build_event(payload)
        self.assertIsNotNone(event)
        self.assertTrue(event["error_identity"].startswith("module:not_found:"))

    def test_diagnostic_stderr_is_captured_without_success_flag(self) -> None:
        payload = self.payload()
        payload["tool_response"] = {
            "stderr": "ModuleNotFoundError: No module named 'yaml'",
        }
        event = capture_hook.build_event(payload)
        self.assertIsNotNone(event)
        self.assertNotEqual(event["message_template"], "structured tool failure")

    def test_same_exception_ignores_traceback_path_line_and_request_id(self) -> None:
        first = self.payload()
        first["tool_response"] = {
            "success": False,
            "stderr": (
                'Traceback (most recent call last):\n'
                '  File "C:\\Users\\alice\\repo-a\\worker.py", line 14, in run\n'
                "ValueError: invalid widget request_id=req-111"
            ),
        }
        second = self.payload()
        second["tool_response"] = {
            "success": False,
            "stderr": (
                'Traceback (most recent call last):\n'
                '  File "D:\\build\\other\\worker.py", line 982, in run\n'
                "ValueError: invalid widget request_id=req-999"
            ),
        }
        first_event = capture_hook.build_event(first)
        second_event = capture_hook.build_event(second)
        self.assertEqual(first_event["error_identity"], second_event["error_identity"])
        self.assertEqual(first_event["signature"], second_event["signature"])

    def test_different_missing_modules_have_different_identities(self) -> None:
        first = self.payload()
        second = self.payload()
        second["tool_response"] = {
            "success": False,
            "stderr": "ModuleNotFoundError: No module named 'pdf2image'",
        }
        first_event = capture_hook.build_event(first)
        second_event = capture_hook.build_event(second)
        self.assertNotEqual(first_event["error_identity"], second_event["error_identity"])
        self.assertNotEqual(first_event["signature"], second_event["signature"])

    def test_wait_agent_poll_timeout_is_not_a_failure(self) -> None:
        payload = self.payload()
        payload["tool_name"] = "collaborationwait_agent"
        payload["tool_response"] = {"message": "Wait timed out.", "timed_out": True}
        self.assertIsNone(capture_hook.build_event(payload))

    def test_explicit_error_content_is_captured(self) -> None:
        payload = self.payload()
        payload["tool_response"] = {
            "content": [
                {
                    "type": "error",
                    "text": "ModuleNotFoundError: No module named 'yaml'",
                }
            ]
        }
        event = capture_hook.build_event(payload)
        self.assertIsNotNone(event)
        self.assertIn("ModuleNotFoundError", event["message_template"])

    def test_error_words_in_success_content_are_not_captured(self) -> None:
        payload = self.payload()
        payload["tool_response"] = {
            "success": True,
            "status": "ok",
            "content": [
                {
                    "type": "text",
                    "text": "Documentation about error, timeout, and permission denied.",
                }
            ],
        }
        self.assertIsNone(capture_hook.build_event(payload))

    def test_explicit_success_overrides_nested_exception_documentation(self) -> None:
        payload = self.payload()
        payload["tool_response"] = {
            "success": True,
            "exception": {
                "type": "ValueError",
                "message": "Example exception from documentation",
            },
        }
        self.assertIsNone(capture_hook.build_event(payload))

    def test_specific_exception_precedes_generic_exit_code(self) -> None:
        first = self.payload()
        first["tool_response"] = (
            "Exit code: 1\nModuleNotFoundError: No module named 'yaml'"
        )
        second = self.payload()
        second["tool_response"] = (
            "Exit code: 1\nModuleNotFoundError: No module named 'pdf2image'"
        )
        first_event = capture_hook.build_event(first)
        second_event = capture_hook.build_event(second)
        self.assertTrue(first_event["error_identity"].startswith("module:not_found:"))
        self.assertTrue(second_event["error_identity"].startswith("module:not_found:"))
        self.assertNotEqual(first_event["error_identity"], second_event["error_identity"])

    def test_windowsapps_pwsh_createprocess_access_denied_has_launcher_identity(self) -> None:
        payload = self.payload()
        payload["tool_name"] = "shell_command"
        payload["tool_input"] = {
            "command": "Get-Content -Raw failure-learning\\SKILL.md"
        }
        payload["tool_response"] = {
            "success": False,
            "stderr": (
                "execution error: windows sandbox runner failed during SpawnChild: "
                "CreateProcessAsUserW failed: 5 (Access is denied) | "
                r"cmd=C:\Users\alice\AppData\Local\Microsoft\WindowsApps\pwsh.exe "
                "-NoProfile -Command Get-Content "
                "(Windows error 5)"
            ),
        }
        event = capture_hook.build_event(payload)
        self.assertIsNotNone(event)
        self.assertEqual(
            event["error_identity"],
            "launcher-shim-unavailable",
        )
        self.assertEqual(event["operation_class"], "shell:get-content")
        self.assertNotIn("WindowsApps", event["message_template"])
        self.assertNotIn(r"C:\Users\alice", event["message_template"])

    def test_signature_separates_exact_tool_and_repository(self) -> None:
        base = self.payload()
        other_tool = self.payload()
        other_tool["tool_name"] = "mcp__browser__js"
        other_repo = self.payload()
        other_repo["cwd"] = r"C:\Users\alice\repo-b"
        base_event = capture_hook.build_event(base)
        tool_event = capture_hook.build_event(other_tool)
        repo_event = capture_hook.build_event(other_repo)
        self.assertNotEqual(base_event["signature"], tool_event["signature"])
        self.assertNotEqual(base_event["signature"], repo_event["signature"])

    def test_mcp_operations_are_tool_specific(self) -> None:
        self.assertEqual(
            capture_hook.operation_class("mcp__node_repl__js", {}),
            "mcp:node_repl:js",
        )
        self.assertEqual(
            capture_hook.operation_class("mcp__node_repl__js_reset", {}),
            "mcp:node_repl:js_reset",
        )
        self.assertNotEqual(
            capture_hook.operation_class("mcp__node_repl__js", {}),
            capture_hook.operation_class("mcp__browser__navigate", {}),
        )

    def test_missing_ids_use_stable_but_discriminating_fallback(self) -> None:
        first = self.payload()
        for key in ("session_id", "turn_id", "tool_use_id"):
            first.pop(key)
        duplicate = json.loads(json.dumps(first))
        different_call = json.loads(json.dumps(first))
        different_call["tool_input"] = {"code": "different_work()"}
        first_event = capture_hook.build_event(first)
        duplicate_event = capture_hook.build_event(duplicate)
        different_event = capture_hook.build_event(different_call)
        self.assertEqual(first_event["idempotency_key"], duplicate_event["idempotency_key"])
        self.assertNotEqual(first_event["idempotency_key"], different_event["idempotency_key"])

    def test_failure_template_keeps_existing_redaction_boundaries(self) -> None:
        payload = self.payload()
        payload["tool_response"] = {
            "success": False,
            "stderr": (
                "Access denied for alice@example.com at "
                "https://example.test/path?token=topsecret "
                "authorization=Bearer abcdefghijklmnopqrstuvwxyz123456"
            ),
        }
        event = capture_hook.build_event(payload)
        serialized = json.dumps(event)
        self.assertNotIn("alice@example.com", serialized)
        self.assertNotIn("topsecret", serialized)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz123456", serialized)
        self.assertNotIn("repo-a", serialized)

    def test_failure_template_redacts_all_absolute_path_families(self) -> None:
        payload = self.payload()
        payload["tool_response"] = {
            "success": False,
            "stderr": (
                "Execution error at D:\\build\\private\\worker.py, "
                "\\\\fileserver\\secret-share\\client\\record.txt, and "
                "/opt/private/client/record.txt. "
                "See https://example.test/help/path?case=private-value"
            ),
        }
        event = capture_hook.build_event(payload)
        self.assertIsNotNone(event)
        rendered = event["message_template"]
        self.assertNotIn("D:\\build\\private\\worker.py", rendered)
        self.assertNotIn("\\\\fileserver\\secret-share", rendered)
        self.assertNotIn("/opt/private/client", rendered)
        self.assertNotIn("private-value", rendered)
        self.assertGreaterEqual(rendered.count("<PATH>"), 3)
        self.assertIn("https://example.test/help/path?case=<REDACTED>", rendered)

    def test_failure_template_redacts_complete_authorization_values(self) -> None:
        payload = self.payload()
        payload["tool_response"] = {
            "success": False,
            "stderr": (
                "Authorization: Bearer short-token "
                '"Authorization": "Basic dXNlcjpwYXNz" '
                "Bearer standalone-token Basic YWJjOjEyMw=="
            ),
        }
        event = capture_hook.build_event(payload)
        self.assertIsNotNone(event)
        rendered = event["message_template"]
        for secret in (
            "short-token",
            "dXNlcjpwYXNz",
            "standalone-token",
            "YWJjOjEyMw==",
        ):
            self.assertNotIn(secret, rendered)
        self.assertNotRegex(
            rendered,
            r"(?i)(?:authorization\s*[:=]\s*|bearer\s+|basic\s+)"
            r"(?!<REDACTED>)[^\s,;]+",
        )

    def test_credential_scanner_redacts_json_escaped_and_nested_values(self) -> None:
        canary = "pw7-canary"
        unsafe_values = (
            f'{{"password":"{canary}"}}',
            f"{{'password':'{canary}'}}",
            f'{{"pass\\u0077ord":"{canary}"}}',
            f'{{"outer":{{"api_key":"{canary}"}}}}',
            rf'{{\"password\":\"{canary}\"}}',
            rf'{{\"pass\\u0077ord\":\"{canary}\"}}',
            rf'{{\\\"outer\\\":{{\\\"access_token\\\":\\\"{canary}\\\"}}}}',
            rf'{{\"password\":{{\"value\":\"{canary}\",\"more\":[1,2]}}}}',
            f'credentials["password"] = "{canary}"',
            f'credentials.password = {{"value": "{canary}", "more": [1, 2]}}',
            f'{{"password": ["{canary}", {{"nested": true}}]}}',
            f'password="unterminated {canary}',
        )
        for unsafe in unsafe_values:
            with self.subTest(unsafe=unsafe):
                safe, changed = capture_hook.sanitize_text(unsafe)
                self.assertTrue(changed)
                self.assertNotIn(canary, safe)
                failure_store._validate_message_template(safe)
                self.assertEqual(capture_hook.sanitize_text(safe)[0], safe)

        for non_secret_key in (
            'password_policy="keep"',
            'password\\u005fpolicy="keep"',
            'secret_scan="keep"',
        ):
            with self.subTest(non_secret_key=non_secret_key):
                self.assertEqual(
                    capture_hook.sanitize_text(non_secret_key)[0],
                    non_secret_key,
                )

    def test_recovery_envelope_contains_no_raw_input_or_output(self) -> None:
        payload = self.payload()
        payload["tool_response"] = {
            "success": True,
            "exit_code": 0,
            "output": "secret-output-value",
        }
        payload["tool_input"] = {"command": "secret-input-value"}
        envelope = capture_hook.build_recovery_envelope(payload)
        self.assertIsNotNone(envelope)
        self.assertEqual(envelope["event_type"], "recovery")
        serialized = json.dumps(envelope)
        self.assertNotIn("secret-output-value", serialized)
        self.assertNotIn("secret-input-value", serialized)
        self.assertFalse(envelope["safety"]["raw_input_stored"])
        self.assertFalse(envelope["safety"]["raw_output_stored"])

    def test_hook_spools_failure_and_recovery_without_creating_database(self) -> None:
        failure_store.provision_identity_key()
        env = os.environ.copy()
        env["CODEX_FAILURE_LEARNING_HOME"] = self.temp.name
        failure = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "capture_hook.py")],
            input=json.dumps(self.payload()),
            text=True,
            capture_output=True,
            env=env,
            timeout=5,
        )
        recovery_payload = self.payload()
        recovery_payload["tool_use_id"] = "call-2"
        recovery_payload["tool_response"] = {"success": True, "exit_code": 0, "output": "ok"}
        recovery = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "capture_hook.py")],
            input=json.dumps(recovery_payload),
            text=True,
            capture_output=True,
            env=env,
            timeout=5,
        )
        self.assertEqual(failure.returncode, 0)
        self.assertEqual(recovery.returncode, 0)
        self.assertFalse((Path(self.temp.name) / "failure-learning.db").exists())
        self.assertTrue((Path(self.temp.name) / "identity.key").exists())
        self.assertEqual(
            {path.name for path in Path(self.temp.name).iterdir()},
            {"identity.key", ".identity.lock", "spool"},
        )
        envelopes = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in (Path(self.temp.name) / "spool").glob("*.json")
        ]
        self.assertEqual({item["event_type"] for item in envelopes}, {"failure", "recovery"})
        self.assertTrue(all(item["auth_version"] == 1 for item in envelopes))
        self.assertTrue(all(failure_store.verify_spool_envelope_auth(item) for item in envelopes))


if __name__ == "__main__":
    unittest.main()
