#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_local_format_rebuilder import FormatRebuilderTests


SCRIPT = Path(__file__).with_name("local_pipeline.py")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class LocalPipelineTests(unittest.TestCase):
    def run_cli(self, *args: str) -> tuple[subprocess.CompletedProcess, dict]:
        process = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            text=True, encoding="utf-8", capture_output=True, check=False)
        return process, json.loads(process.stdout)

    def test_approved_xlsx_rebuild_and_source_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.xlsx"
            normalized = root / "draft.md"
            manifest = root / "draft.review.json"
            output = root / "rebuilt.xlsx"
            fixture = FormatRebuilderTests()
            fixture.make_xlsx(source)
            normalized.write_text(
                "## Sheet: Customers\n- A1={humannameA} | B1={mailaddressA}\n",
                encoding="utf-8")
            manifest.write_text(json.dumps({
                "schema_version": 1, "status": "approved",
                "source_path": str(source), "draft_path": str(normalized),
                "format": "xlsx", "reviewed_at": "2026-08-04T00:00:00+00:00",
                "source_sha256": sha256(source), "draft_sha256": sha256(normalized),
            }), encoding="utf-8")
            before = sha256(source)
            process, result = self.run_cli(
                "rebuild", "--input", str(source), "--normalized", str(normalized),
                "--manifest", str(manifest), "--output", str(output))
            self.assertEqual(process.returncode, 0)
            self.assertEqual(result["status"], "rebuilt")
            self.assertEqual(before, sha256(source))
            self.assertTrue(output.is_file())

    def test_pending_manifest_and_pdf_rebuild_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.pdf"
            normalized = root / "draft.md"
            manifest = root / "draft.review.json"
            source.write_bytes(b"%PDF synthetic")
            normalized.write_text("normalized", encoding="utf-8")
            manifest.write_text(json.dumps({"schema_version": 1, "status": "pending_review"}), encoding="utf-8")
            process, result = self.run_cli(
                "rebuild", "--input", str(source), "--normalized", str(normalized),
                "--manifest", str(manifest), "--output", str(root / "out.pdf"))
            self.assertEqual(process.returncode, 2)
            self.assertEqual(result["error_code"], "format_rebuild_not_supported")


if __name__ == "__main__":
    unittest.main()
