from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import skill_history


class SkillHistoryTests(unittest.TestCase):
    def test_snapshot_is_deduplicated_and_contains_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "state"
            with patch.object(skill_history, "state_root", return_value=root):
                first = skill_history.snapshot(
                    "build-decision-ready-materials",
                    skill_history.DEFAULT_REGISTRY,
                    root,
                )
                second = skill_history.snapshot(
                    "build-decision-ready-materials",
                    skill_history.DEFAULT_REGISTRY,
                    root,
                )
            self.assertTrue(first["inserted"])
            self.assertFalse(second["inserted"])
            self.assertTrue(first["file_manifest"])
            self.assertTrue(first["content_fingerprint"].startswith("sha256:"))

    def test_backfill_marks_reconstruction_as_inferred(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            evidence = base / "evals" / "2026-07-31"
            evidence.mkdir(parents=True)
            (evidence / "result.json").write_text("{}", encoding="utf-8")
            root = base / "state"
            result = skill_history.backfill("example-skill", evidence.parent, root)
            events = skill_history.history("example-skill", root)
            self.assertEqual(result, {"discovered": 1, "inserted": 1})
            self.assertEqual(events[0]["provenance"], "inferred")
            self.assertEqual(events[0]["content_fingerprint"], "")
            self.assertNotIn("result.json", json.dumps(events))


if __name__ == "__main__":
    unittest.main()
