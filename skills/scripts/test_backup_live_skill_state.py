#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from backup_live_skill_state import run


class LiveStateBackupTests(unittest.TestCase):
    def test_online_backup_is_integrity_checked_and_hooks_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source.sqlite3"
            db = sqlite3.connect(source)
            try:
                db.execute("CREATE TABLE evidence(id INTEGER PRIMARY KEY, value TEXT)")
                db.execute("INSERT INTO evidence(value) VALUES('bounded')")
                db.commit()
            finally:
                db.close()
            hooks = root / "hooks.json"
            hooks.write_text(json.dumps({"hooks": {"Stop": []}}), encoding="utf-8")

            result = run(
                root / "backup",
                hooks,
                [("ledger.sqlite3", source)],
            )

            backup = Path(result["databases"][0]["backup"])
            self.assertEqual(result["databases"][0]["integrity"], "ok")
            db = sqlite3.connect(backup)
            try:
                self.assertEqual(
                    db.execute("SELECT value FROM evidence").fetchone()[0], "bounded"
                )
            finally:
                db.close()
            self.assertEqual(
                (root / "backup" / "hooks.pre-cutover.json").read_text(encoding="utf-8"),
                hooks.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
