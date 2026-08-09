from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = SKILL_ROOT / "scripts" / "configure_hooks.py"


def load_module():
    name = "name_work_sessions_configure_hooks"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ConfigureHooksTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.configure = load_module()

    def test_install_is_idempotent_and_preserves_unrelated_config(self) -> None:
        foreign_session_end = {
            "matcher": "other",
            "hooks": [
                {
                    "type": "command",
                    "command": "python C:/other/session_end.py",
                    "timeout": 2,
                }
            ],
        }
        foreign_stop = {
            "hooks": [{"type": "command", "command": "python C:/other/stop.py"}]
        }
        foreign_session_start = {
            "matcher": "startup",
            "hooks": [
                {
                    "type": "command",
                    "command": "python C:/other/session_start.py",
                }
            ],
        }
        stale_ours = self.configure._hook_definition()
        stale_ours["matcher"] = "*"
        stale_ours["hooks"][0]["timeout"] = 99
        duplicate_ours = self.configure._hook_definition()
        initial = {
            "description": "keep this description",
            "customTopLevel": {"keep": True},
            "hooks": {
                "SessionEnd": [foreign_session_end, stale_ours, duplicate_ours],
                "SessionStart": [foreign_session_start],
                "Stop": [foreign_stop],
            },
        }

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "hooks.json"
            path.write_text(
                json.dumps(initial, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            first_changed = self.configure.install_hook(path)
            after_first_bytes = path.read_bytes()
            first = json.loads(after_first_bytes.decode("utf-8"))
            second_changed = self.configure.install_hook(path)
            after_second_bytes = path.read_bytes()
            temporary_residue = list(path.parent.glob(f".{path.name}.*.tmp"))

        self.assertTrue(first_changed)
        self.assertFalse(second_changed)
        self.assertEqual(after_first_bytes, after_second_bytes)
        self.assertEqual("keep this description", first["description"])
        self.assertEqual({"keep": True}, first["customTopLevel"])
        self.assertEqual([foreign_stop], first["hooks"]["Stop"])
        session_end = first["hooks"]["SessionEnd"]
        self.assertEqual(2, len(session_end))
        self.assertEqual(foreign_session_end, session_end[0])
        ours = [entry for entry in session_end if self.configure._is_our_hook(entry)]
        self.assertEqual([self.configure._hook_definition()], ours)
        session_start = first["hooks"]["SessionStart"]
        self.assertEqual(2, len(session_start))
        self.assertEqual(foreign_session_start, session_start[0])
        self.assertEqual(
            self.configure._hook_definition("SessionStart"), session_start[1]
        )
        self.assertEqual([], temporary_residue)

    def test_uninstall_removes_only_managed_hook_and_is_idempotent(self) -> None:
        foreign = {
            "hooks": [
                {"type": "command", "command": "python C:/other/session_end.py"}
            ]
        }
        initial = {
            "hooks": {
                "SessionEnd": [foreign, self.configure._hook_definition()],
                "SessionStart": [
                    foreign,
                    self.configure._hook_definition("SessionStart"),
                ],
                "Stop": [{"hooks": [{"command": "keep-stop"}]}],
            }
        }

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "hooks.json"
            path.write_text(json.dumps(initial), encoding="utf-8")

            self.assertTrue(self.configure.uninstall_hook(path))
            after_first = json.loads(path.read_text(encoding="utf-8"))
            first_bytes = path.read_bytes()
            self.assertFalse(self.configure.uninstall_hook(path))
            second_bytes = path.read_bytes()

        self.assertEqual([foreign], after_first["hooks"]["SessionEnd"])
        self.assertEqual([foreign], after_first["hooks"]["SessionStart"])
        self.assertEqual(initial["hooks"]["Stop"], after_first["hooks"]["Stop"])
        self.assertEqual(first_bytes, second_bytes)

    def test_status_distinguishes_configured_from_observed_and_does_not_claim_trust(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            hooks_path = root / "hooks.json"
            self.assertTrue(self.configure.install_hook(hooks_path))
            configured = self.configure.hook_status(hooks_path)
            with mock.patch.dict(
                "os.environ",
                {"CODEX_SESSION_NAMING_HOME": str(root / "state")},
                clear=False,
            ):
                observed = self.configure._observed_events()

        self.assertTrue(configured["configured"])
        self.assertTrue(configured["triggers"]["sessionEndOther"])
        self.assertTrue(configured["triggers"]["sessionStartResume"])
        self.assertEqual("unknown", configured["trust"]["state"])
        self.assertFalse(configured["trust"]["verified"])
        self.assertFalse(observed["sessionEndOther"])
        self.assertFalse(observed["sessionStartResume"])
        self.assertFalse(observed["conclusiveWhenFalse"])

    def test_invalid_hooks_json_fails_closed_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "hooks.json"
            original = b"{invalid-json"
            path.write_bytes(original)

            with self.assertRaisesRegex(
                RuntimeError, "hooks-file-is-not-valid-json"
            ):
                self.configure.install_hook(path)

            self.assertEqual(original, path.read_bytes())

    def test_task_xml_is_bounded_hidden_and_uses_the_managed_router(self) -> None:
        xml = self.configure.build_task_xml(datetime(2026, 7, 31, 12, 0, 0))

        self.assertIn(self.configure.TASK_MARKER, xml)
        self.assertIn("<Interval>PT1M</Interval>", xml)
        self.assertIn("<ExecutionTimeLimit>PT5M</ExecutionTimeLimit>", xml)
        self.assertIn("<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>", xml)
        self.assertIn("<RunLevel>LeastPrivilege</RunLevel>", xml)
        self.assertIn("<Hidden>true</Hidden>", xml)
        self.assertIn("batch_router.py", xml)
        self.assertIn("router-config.json", xml)
        self.assertIn("--execute-adapters", xml)
        self.assertNotIn("session_end_hook.py", xml)

    def test_foreign_task_is_never_overwritten_or_deleted(self) -> None:
        with (
            mock.patch.object(
                self.configure,
                "task_status",
                return_value={"installed": True, "supported": True, "owned": False},
            ),
            mock.patch.object(self.configure, "_run_schtasks") as run,
        ):
            with self.assertRaisesRegex(
                RuntimeError, "task-name-is-owned-by-another-program"
            ):
                self.configure.install_task()
            with self.assertRaisesRegex(
                RuntimeError, "refusing-to-delete-unowned-task"
            ):
                self.configure.uninstall_task()

        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
