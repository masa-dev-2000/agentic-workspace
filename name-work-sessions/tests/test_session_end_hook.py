from __future__ import annotations

import importlib.util
import inspect
import io
import json
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = SKILL_ROOT / "scripts" / "session_end_hook.py"


def load_module():
    name = "name_work_sessions_session_end_hook"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class SessionEndHookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.hook = load_module()

    def test_spools_body_free_event_without_starting_work(self) -> None:
        raw = json.dumps(
            {
                "session_id": "session-safe-123",
                "hook_event_name": "SessionEnd",
                "transcript_path": r"C:\private\conversation.jsonl",
                "cwd": r"C:\clients\secret-project",
                "model": "gpt-test",
                "reason": "other",
                "prompt": "TOP SECRET prompt body",
                "assistant_response": "TOP SECRET response body",
                "api_key": "sk-do-not-store-this",
            }
        ).encode("utf-8")
        with tempfile.TemporaryDirectory() as temp:
            state_root = Path(temp)
            status = self.hook.handle(raw, state_root=state_root)

            self.assertEqual("stored", status)
            events = list((state_root / "spool").glob("*.json"))
            self.assertEqual(1, len(events))
            payload_bytes = events[0].read_bytes()
            payload = json.loads(payload_bytes.decode("utf-8"))

        self.assertEqual("lifecycle.session-end", payload["eventType"])
        self.assertEqual("session-safe-123", payload["correlation"]["sessionId"])
        self.assertFalse(payload["privacy"]["rawContentStored"])
        self.assertEqual(len(raw), payload["metadata"]["contentBytesDiscarded"])
        stored_text = payload_bytes.decode("utf-8")
        for secret in (
            "TOP SECRET",
            "sk-do-not-store-this",
            "conversation.jsonl",
            "secret-project",
            "gpt-test",
        ):
            self.assertNotIn(secret, stored_text)

    def test_malformed_input_is_fail_open_and_not_written(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            status = self.hook.handle(b"{not-json", state_root=Path(temp))

            self.assertEqual("invalid-input", status)
            self.assertFalse((Path(temp) / "spool").exists())

    def test_resume_session_start_spools_body_free_fallback_event(self) -> None:
        raw = json.dumps(
            {
                "session_id": "session-resumed-123",
                "hook_event_name": "SessionStart",
                "source": "resume",
                "prompt": "TOP SECRET resumed prompt",
            }
        ).encode("utf-8")
        with tempfile.TemporaryDirectory() as temp:
            state_root = Path(temp)
            status = self.hook.handle(
                raw,
                state_root=state_root,
                event_name="SessionStart",
                required_source="resume",
            )
            events = list((state_root / "spool").glob("*.json"))
            self.assertEqual(1, len(events))
            payload_bytes = events[0].read_bytes()
            payload = json.loads(payload_bytes.decode("utf-8"))

        self.assertEqual("stored", status)
        self.assertEqual("lifecycle.session-start", payload["eventType"])
        self.assertEqual("SessionStart", payload["source"]["name"])
        self.assertEqual("resume", payload["metadata"]["status"])
        self.assertFalse(payload["privacy"]["rawContentStored"])
        self.assertNotIn("TOP SECRET", payload_bytes.decode("utf-8"))

    def test_nonresume_session_start_is_ignored_without_spooling(self) -> None:
        raw = json.dumps(
            {
                "session_id": "session-new-123",
                "hook_event_name": "SessionStart",
                "source": "startup",
            }
        ).encode("utf-8")
        with tempfile.TemporaryDirectory() as temp:
            state_root = Path(temp)
            status = self.hook.handle(
                raw,
                state_root=state_root,
                event_name="SessionStart",
                required_source="resume",
            )

            self.assertEqual("ignored-source", status)
            self.assertFalse((state_root / "spool").exists())

    def test_oversized_input_is_fail_open_and_not_written(self) -> None:
        raw = b"x" * (self.hook.MAX_INPUT_BYTES + 1)
        with tempfile.TemporaryDirectory() as temp:
            started = time.perf_counter()
            status = self.hook.handle(raw, state_root=Path(temp))
            elapsed = time.perf_counter() - started

            self.assertEqual("input-too-large", status)
            self.assertLess(elapsed, 0.5)
            self.assertFalse((Path(temp) / "spool").exists())

    def test_dispatcher_failure_remains_fail_open(self) -> None:
        with mock.patch.object(
            self.hook, "_load_dispatcher", side_effect=RuntimeError("synthetic")
        ):
            status = self.hook.handle(b"{}", state_root=Path("unused"))

        self.assertEqual("hook-error", status)

    def test_duplicate_event_is_deduplicated_without_overwriting_receipt_input(self) -> None:
        first = json.dumps(
            {
                "event_id": "event-stable-1",
                "session_id": "session-first",
                "hook_event_name": "SessionEnd",
            }
        ).encode("utf-8")
        second = json.dumps(
            {
                "event_id": "event-stable-1",
                "session_id": "session-second",
                "hook_event_name": "SessionEnd",
                "prompt": "must not replace the first event",
            }
        ).encode("utf-8")

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual("stored", self.hook.handle(first, state_root=root))
            before = (root / "spool" / "event-stable-1.json").read_bytes()
            self.assertEqual("duplicate", self.hook.handle(second, state_root=root))
            after = (root / "spool" / "event-stable-1.json").read_bytes()

        self.assertEqual(before, after)
        self.assertNotIn(b"session-second", after)
        self.assertNotIn(b"must not replace", after)

    def test_main_exits_zero_and_emits_no_output_even_when_stdin_fails(self) -> None:
        class BrokenBuffer:
            def read(self, _size: int) -> bytes:
                raise OSError("synthetic input failure")

        class BrokenStdin:
            buffer = BrokenBuffer()

        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(sys, "stdin", BrokenStdin()),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            result = self.hook.main([])

        self.assertEqual(0, result)
        self.assertEqual("", stdout.getvalue())
        self.assertEqual("", stderr.getvalue())

    def test_hook_contains_no_worker_or_subprocess_launch(self) -> None:
        source = inspect.getsource(self.hook)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("schtasks", source.lower())
        self.assertFalse(hasattr(self.hook, "_trigger_worker"))


if __name__ == "__main__":
    unittest.main()
