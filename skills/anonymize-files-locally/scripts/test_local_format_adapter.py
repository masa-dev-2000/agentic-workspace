from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
import zipfile


SCRIPT = Path(__file__).with_name("local_format_adapter.py")


class FormatAdapterTests(unittest.TestCase):
    def run_cli(self, *args: str) -> tuple[subprocess.CompletedProcess, dict]:
        process = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            text=True, encoding="utf-8", capture_output=True, check=False,
        )
        import json
        return process, json.loads(process.stdout)

    def test_docx_extracts_without_printing_content(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder, "source.docx")
            output = Path(folder, "normalized.md")
            document = (
                "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
                "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
                "<w:body><w:p><w:r><w:t>Hanako Example</w:t></w:r></w:p></w:body></w:document>"
            ).encode()
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("word/document.xml", document)
            process, result = self.run_cli("extract", "--input", str(source), "--output", str(output))
            self.assertEqual(process.returncode, 0)
            self.assertEqual(result["status"], "extracted")
            self.assertNotIn("Hanako Example", process.stdout)
            self.assertIn("Hanako Example", output.read_text(encoding="utf-8"))

    def test_xlsx_extracts_sheet_and_cell_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder, "source.xlsx")
            output = Path(folder, "normalized.md")
            workbook = (
                "<workbook xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main' "
                "xmlns:r='http://schemas.openxmlformats.org/officeDocument/2006/relationships'>"
                "<sheets><sheet name='Customers' r:id='rId1'/></sheets></workbook>"
            ).encode()
            rels = (
                "<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>"
                "<Relationship Id='rId1' Target='worksheets/sheet1.xml'/></Relationships>"
            ).encode()
            sheet = (
                "<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>"
                "<sheetData><row r='1'><c r='A1' t='str'><v>Hanako Example</v></c></row></sheetData>"
                "</worksheet>"
            ).encode()
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("xl/workbook.xml", workbook)
                archive.writestr("xl/_rels/workbook.xml.rels", rels)
                archive.writestr("xl/worksheets/sheet1.xml", sheet)
            process, result = self.run_cli("extract", "--input", str(source), "--output", str(output))
            self.assertEqual(process.returncode, 0)
            self.assertEqual(result["sheet_count"], 1)
            self.assertEqual(result["unit_count"], 1)
            self.assertIn("Customers", output.read_text(encoding="utf-8"))
            self.assertIn("Hanako Example", output.read_text(encoding="utf-8"))

    def test_rejects_unsupported_and_does_not_leak(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder, "secret.xls")
            source.write_text("TOP SECRET", encoding="utf-8")
            process, result = self.run_cli("extract", "--input", str(source), "--output", str(Path(folder, "x.md")))
            self.assertEqual(process.returncode, 2)
            self.assertEqual(result["error_code"], "unsupported_input")
            self.assertNotIn("TOP SECRET", process.stdout + process.stderr)


if __name__ == "__main__":
    unittest.main()
