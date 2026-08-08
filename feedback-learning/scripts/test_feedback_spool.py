from __future__ import annotations

import contextlib
import io
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from capture_hook import build_record
from configure_hook import (
    cutover_candidate,
    dispatcher_installed,
    installed,
    main as configure_main,
    pm_direct_installed,
    provision_state,
)
from feedback_store import FeedbackStore, nonblocking_process_lock
from spool_contract import MAX_SPOOL_BYTES, SPOOL_FIELDS, validate_record
from stabilize_hook_order import group_role, stabilize


HOOK = Path(__file__).with_name("capture_hook.py")
SKILL_DIR = Path(__file__).resolve().parents[1]


def legacy_database(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    db_path = root / "feedback.sqlite3"
    db = sqlite3.connect(db_path)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute(
            """CREATE TABLE feedback_events(
                feedback_id TEXT PRIMARY KEY,
                observed_at TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                signature TEXT NOT NULL,
                session_hash TEXT NOT NULL,
                turn_hash TEXT NOT NULL,
                repo_hash TEXT NOT NULL,
                feedback_type TEXT NOT NULL,
                subject_class TEXT NOT NULL,
                theme_key TEXT NOT NULL,
                impact TEXT NOT NULL,
                explicitness TEXT NOT NULL,
                capture_mode TEXT NOT NULL,
                expectation_template TEXT NOT NULL,
                observed_template TEXT NOT NULL,
                desired_template TEXT NOT NULL,
                event_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        db.execute("INSERT INTO meta VALUES('schema_version','2')")
        common = (
            "2026-07-31T00:00:00+00:00",
            "signature",
            "",
            "",
            "",
            "request",
            "workflow",
            "request-workflow",
            "medium",
            "explicit",
            "",
            "",
            "{}",
            "2026-07-31T00:00:00+00:00",
        )
        db.execute(
            """INSERT INTO feedback_events VALUES(
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )""",
            (
                "fb_hook",
                common[0],
                "legacy-hook",
                *common[1:10],
                "hook",
                "LEGACY_HOOK_EXPECTATION_SECRET",
                "LEGACY_HOOK_OBSERVED_SECRET",
                "LEGACY_HOOK_DESIRED_SECRET",
                *common[12:],
            ),
        )
        db.execute(
            """INSERT INTO feedback_events VALUES(
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )""",
            (
                "fb_manual",
                common[0],
                "legacy-manual",
                *common[1:10],
                "manual",
                "manual expectation summary",
                "",
                "manual desired summary",
                *common[12:],
            ),
        )
        db.commit()
    finally:
        db.close()
    return db_path


class FeedbackSpoolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, root: Path, payload: dict) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["CODEX_FEEDBACK_LEARNING_HOME"] = str(root)
        return subprocess.run(
            [sys.executable, "-X", "utf8", str(HOOK)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )

    @staticmethod
    def payload(turn: str = "turn-1") -> dict:
        return {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "ダメじゃん。同じ失敗を繰り返さず事例登録して。",
            "session_id": "session-1",
            "turn_id": turn,
            "cwd": r"C:\repo",
        }

    @staticmethod
    def spool_files(root: Path) -> list[Path]:
        spool = root / "spool"
        return sorted(spool.glob("*.json")) if spool.is_dir() else []

    def provision_key_only(self, root: Path) -> bytes:
        root.mkdir(parents=True)
        key = secrets.token_bytes(32)
        (root / "hmac.key").write_text(key.hex(), encoding="ascii")
        return key

    def test_missing_key_drops_without_creating_state(self):
        root = self.base / "missing-key"
        result = self.run_hook(root, self.payload())
        self.assertEqual(0, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertEqual("", result.stderr)
        self.assertFalse(root.exists())

    def test_hook_creates_only_body_free_signed_spool_not_database(self):
        root = self.base / "hook-only"
        key = self.provision_key_only(root)
        result = self.run_hook(root, self.payload())
        self.assertEqual(0, result.returncode)
        self.assertFalse((root / "feedback.sqlite3").exists())
        files = self.spool_files(root)
        self.assertEqual(1, len(files))
        record = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(SPOOL_FIELDS, set(record))
        self.assertEqual(record, validate_record(record, key))
        rendered = json.dumps(record, ensure_ascii=False)
        self.assertNotIn("ダメじゃん", rendered)
        self.assertNotIn("事例登録", rendered)
        self.assertFalse(
            {"prompt", "desired_template", "observed_template"} & set(record)
        )

        wrong_event = self.payload("turn-2")
        wrong_event["hook_event_name"] = "SessionStart"
        self.run_hook(root, wrong_event)
        self.assertEqual(1, len(self.spool_files(root)))

    def test_authenticated_drain_writes_body_free_trusted_event_and_receipt(self):
        root = self.base / "drain"
        store = FeedbackStore(root)
        store.initialize()
        self.run_hook(root, self.payload())
        result = store.drain_spool()
        self.assertEqual(1, result["processed"])
        event = store.rows("SELECT * FROM feedback_events")[0]
        self.assertEqual("hook", event["capture_mode"])
        self.assertEqual("trusted", event["provenance_trust"])
        self.assertEqual("", event["expectation_template"])
        self.assertEqual("", event["observed_template"])
        self.assertEqual("", event["desired_template"])
        self.assertTrue(json.loads(event["event_json"])["body_free_hook"])
        self.assertEqual(1, len(store.rows("SELECT * FROM spool_receipts")))

    def test_tamper_is_quarantined_without_receipt(self):
        root = self.base / "tamper"
        store = FeedbackStore(root)
        store.initialize()
        self.run_hook(root, self.payload())
        path = self.spool_files(root)[0]
        record = json.loads(path.read_text(encoding="utf-8"))
        record["impact"] = "high"
        path.write_text(json.dumps(record), encoding="utf-8")
        result = store.drain_spool()
        self.assertEqual(1, result["rejected"])
        self.assertEqual([], store.rows("SELECT * FROM feedback_events"))
        self.assertEqual([], store.rows("SELECT * FROM spool_receipts"))
        rejected = next((root / "spool").glob("*.rejected"))
        body = rejected.read_text(encoding="ascii")
        self.assertIn("invalid-authentication", body)
        self.assertNotIn("auth_tag", body)

    def test_unknown_field_and_oversize_are_body_free_quarantine(self):
        root = self.base / "invalid"
        store = FeedbackStore(root)
        store.initialize()
        self.run_hook(root, self.payload())
        unknown = self.spool_files(root)[0]
        record = json.loads(unknown.read_text(encoding="utf-8"))
        record["prompt"] = "SHOULD_NOT_SURVIVE_QUARANTINE"
        unknown.write_text(json.dumps(record), encoding="utf-8")
        oversize = root / "spool" / "oversize.json"
        oversize.write_bytes(b"OVERSIZE_SECRET" * (MAX_SPOOL_BYTES // 4))
        result = store.drain_spool()
        self.assertEqual(2, result["rejected"])
        quarantines = list((root / "spool").glob("*.rejected"))
        self.assertEqual(2, len(quarantines))
        bodies = "\n".join(path.read_text(encoding="ascii") for path in quarantines)
        self.assertNotIn("SHOULD_NOT_SURVIVE_QUARANTINE", bodies)
        self.assertNotIn("OVERSIZE_SECRET", bodies)
        self.assertEqual([], store.rows("SELECT * FROM spool_receipts"))

    def test_duplicate_has_one_receipt_and_one_event(self):
        root = self.base / "duplicate"
        store = FeedbackStore(root)
        store.initialize()
        self.run_hook(root, self.payload())
        original = self.spool_files(root)[0]
        shutil.copyfile(original, root / "spool" / "duplicate.json")
        result = store.drain_spool()
        self.assertEqual(1, result["processed"])
        self.assertEqual(1, result["duplicates"])
        self.assertEqual(1, len(store.rows("SELECT * FROM feedback_events")))
        self.assertEqual(1, len(store.rows("SELECT * FROM spool_receipts")))

    def test_nonblocking_single_drainer_lock_defers_work(self):
        root = self.base / "concurrency"
        store = FeedbackStore(root)
        store.initialize()
        self.run_hook(root, self.payload())
        with nonblocking_process_lock(store.drain_lock_path) as acquired:
            self.assertTrue(acquired)
            result = store.drain_spool()
            self.assertEqual(1, result["busy"])
            self.assertEqual(1, result["deferred"])
        self.assertEqual(1, store.drain_spool()["processed"])

    def test_legacy_migration_blanks_hook_text_and_marks_unverified(self):
        root = self.base / "legacy"
        db_path = legacy_database(root)
        store = FeedbackStore(root)
        store.initialize()
        store.initialize()
        rows = {
            row["feedback_id"]: row
            for row in store.rows(
                """SELECT feedback_id,capture_mode,expectation_template,
                          observed_template,desired_template,provenance_trust
                   FROM feedback_events"""
            )
        }
        self.assertEqual("", rows["fb_hook"]["expectation_template"])
        self.assertEqual("", rows["fb_hook"]["observed_template"])
        self.assertEqual("", rows["fb_hook"]["desired_template"])
        self.assertEqual(
            "manual desired summary",
            rows["fb_manual"]["desired_template"],
        )
        self.assertEqual(
            {"legacy-unverified"},
            {row["provenance_trust"] for row in rows.values()},
        )
        status = store.status()
        self.assertEqual("4", status["schema_version"])
        self.assertEqual("3", status["privacy_repair_version"])
        self.assertEqual([], store.build_patterns())
        for path in (db_path, db_path.with_name(db_path.name + "-wal")):
            if path.exists():
                self.assertNotIn(
                    b"LEGACY_HOOK_DESIRED_SECRET",
                    path.read_bytes(),
                )

    def test_manual_feedback_is_trusted_and_keeps_sanitized_summary(self):
        root = self.base / "manual"
        store = FeedbackStore(root)
        feedback_id, inserted = store.add_feedback(
            {
                "feedback_type": "request",
                "subject_class": "workflow",
                "capture_mode": "manual",
                "desired_template": "bounded manual summary",
            }
        )
        self.assertTrue(inserted)
        row = store.rows(
            """SELECT provenance_trust,desired_template
               FROM feedback_events WHERE feedback_id=?""",
            (feedback_id,),
        )[0]
        self.assertEqual("trusted", row["provenance_trust"])
        self.assertEqual("bounded manual summary", row["desired_template"])
        with self.assertRaises(ValueError):
            store.add_feedback(
                {
                    "feedback_type": "request",
                    "subject_class": "workflow",
                    "capture_mode": "hook",
                    "desired_template": "Hook mode is runtime-only",
                }
            )

    def test_cutover_preserves_unrelated_and_pm_direct_then_removes_dispatcher(self):
        dispatcher = (
            r'python -X utf8 "C:\plugins\ai-project-manager\hooks'
            r'\user_prompt_dispatcher.py"'
        )
        config = {
            "description": "existing",
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "*",
                        "hooks": [{"type": "command", "command": "existing.py"}],
                    }
                ],
                "UserPromptSubmit": [
                    {"hooks": [{"type": "command", "command": "before.py"}]},
                    {
                        "matcher": "*",
                        "hooks": [
                            {
                                "type": "command",
                                "command": dispatcher,
                                "commandWindows": dispatcher,
                                "timeout": 3,
                            }
                        ],
                    },
                    {"hooks": [{"type": "command", "command": "telemetry.py"}]},
                ],
            },
        }
        original = json.loads(json.dumps(config))
        candidate, plan = cutover_candidate(config, SKILL_DIR)
        self.assertEqual(original, config)
        self.assertTrue(plan["ready"])
        self.assertTrue(plan["removed_dispatcher"])
        self.assertTrue(installed(candidate))
        self.assertTrue(pm_direct_installed(candidate))
        self.assertFalse(dispatcher_installed(candidate))
        self.assertEqual(
            original["hooks"]["PostToolUse"],
            candidate["hooks"]["PostToolUse"],
        )
        direct_groups = candidate["hooks"]["UserPromptSubmit"]
        self.assertEqual("before.py", direct_groups[0]["hooks"][0]["command"])
        self.assertIn("capture_prompt.py", direct_groups[1]["hooks"][0]["command"])
        self.assertEqual("telemetry.py", direct_groups[2]["hooks"][0]["command"])
        self.assertTrue(
            any(
                "feedback-learning" in hook.get("command", "")
                for hook in direct_groups[3].get("hooks", [])
            )
        )
        self.assertTrue(
            any(
                "capture_prompt.py" in hook.get("command", "")
                for group in direct_groups
                for hook in group.get("hooks", [])
            )
        )
        self.assertTrue(
            any(
                "feedback-learning" in hook.get("command", "")
                for group in direct_groups
                for hook in group.get("hooks", [])
            )
        )

    def test_stabilize_restores_reference_slots_without_losing_direct_hooks(self):
        dispatcher = (
            r'python -X utf8 "C:\plugins\ai-project-manager\hooks'
            r'\user_prompt_dispatcher.py"'
        )
        reference = {
            "hooks": {
                "UserPromptSubmit": [
                    {"hooks": [{"type": "command", "command": "before.py"}]},
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": dispatcher,
                                "commandWindows": dispatcher,
                            }
                        ]
                    },
                    {"hooks": [{"type": "command", "command": "telemetry.py"}]},
                ]
            }
        }
        stable, _ = cutover_candidate(reference, SKILL_DIR)
        groups = stable["hooks"]["UserPromptSubmit"]
        current = json.loads(json.dumps(stable))
        current["hooks"]["UserPromptSubmit"] = [
            groups[0],
            groups[2],
            groups[3],
            groups[1],
        ]

        candidate, plan = stabilize(current, reference, SKILL_DIR)

        self.assertTrue(plan["changed"])
        self.assertEqual(
            ["unrelated", "pm-direct", "unrelated", "feedback"],
            [group_role(group) for group in candidate["hooks"]["UserPromptSubmit"]],
        )
        self.assertEqual(
            stable["hooks"]["UserPromptSubmit"],
            candidate["hooks"]["UserPromptSubmit"],
        )

    def test_cutover_dry_run_changes_no_config_or_state(self):
        config_path = self.base / "codex" / "hooks.json"
        config_path.parent.mkdir()
        dispatcher = (
            r'python -X utf8 "C:\plugins\ai-project-manager\hooks'
            r'\user_prompt_dispatcher.py"'
        )
        config_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "UserPromptSubmit": [
                            {"hooks": [{"type": "command", "command": dispatcher}]}
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        before = config_path.read_bytes()
        state = self.base / "dry-state"
        output = io.StringIO()
        with mock.patch.dict(
            os.environ,
            {"CODEX_FEEDBACK_LEARNING_HOME": str(state)},
            clear=False,
        ), contextlib.redirect_stdout(output):
            self.assertEqual(
                0,
                configure_main(["cutover", "--config", str(config_path)]),
            )
        result = json.loads(output.getvalue())
        self.assertTrue(result["dry_run"])
        self.assertTrue(result["cutover"]["ready"])
        self.assertEqual(before, config_path.read_bytes())
        self.assertFalse(state.exists())

    def test_provision_backs_up_then_migrates_then_drains(self):
        state = self.base / "provision-state"
        legacy_database(state)
        config_path = self.base / "codex" / "hooks.json"
        config_path.parent.mkdir()
        self.assertTrue((state / "feedback.sqlite3").is_file())
        with mock.patch.dict(
            os.environ,
            {"CODEX_FEEDBACK_LEARNING_HOME": str(state)},
            clear=False,
        ):
            result = provision_state(config_path)
        backup = Path(result["migration_backup"])
        self.assertTrue(backup.is_file())
        self.assertEqual(0, result["drain"]["processed"])
        db = sqlite3.connect(backup)
        try:
            backup_hook = db.execute(
                """SELECT desired_template,provenance_trust
                   FROM feedback_events WHERE feedback_id='fb_hook'"""
            ).fetchone()
            backup_manual = db.execute(
                """SELECT desired_template
                   FROM feedback_events WHERE feedback_id='fb_manual'"""
            ).fetchone()
            versions = dict(
                db.execute(
                    """SELECT key,value FROM meta
                       WHERE key IN ('schema_version','privacy_repair_version')"""
                ).fetchall()
            )
        finally:
            db.close()
        self.assertEqual(("", "legacy-unverified"), backup_hook)
        self.assertEqual(("manual desired summary",), backup_manual)
        self.assertEqual("4", versions["schema_version"])
        self.assertEqual("3", versions["privacy_repair_version"])
        live = FeedbackStore(state)
        self.assertEqual("4", live.status()["schema_version"])
        self.assertEqual(
            "",
            live.rows(
                """SELECT desired_template FROM feedback_events
                   WHERE feedback_id='fb_hook'"""
            )[0]["desired_template"],
        )

    def test_build_record_rejects_unknown_event_before_classification(self):
        key = secrets.token_bytes(32)
        payload = self.payload()
        payload["hook_event_name"] = "PreToolUse"
        self.assertIsNone(build_record(payload, key))


if __name__ == "__main__":
    unittest.main()
