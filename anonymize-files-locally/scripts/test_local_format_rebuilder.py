from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile


SCRIPT = Path(__file__).with_name("local_format_rebuilder.py")
ADAPTER = Path(__file__).with_name("local_format_adapter.py")


class FormatRebuilderTests(unittest.TestCase):
    def run_cli(self, script: Path, *args: str) -> tuple[subprocess.CompletedProcess, dict]:
        process = subprocess.run(
            [sys.executable, str(script), *args],
            text=True, encoding="utf-8", capture_output=True, check=False,
        )
        return process, json.loads(process.stdout)

    def make_xlsx(self, path: Path) -> None:
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
            "<sheetData><row r='1'><c r='A1' t='str'><v>Hanako Example</v></c>"
            "<c r='B1' t='str'><v>hanako@example.test</v></c></row></sheetData>"
            "</worksheet>"
        ).encode()
        styles = b"<styleSheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'/>"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("xl/workbook.xml", workbook)
            archive.writestr("xl/_rels/workbook.xml.rels", rels)
            archive.writestr("xl/worksheets/sheet1.xml", sheet)
            archive.writestr("xl/styles.xml", styles)

    def make_docx(self, path: Path) -> None:
        document = (
            "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
            "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
            "<w:body><w:p><w:r><w:rPr><w:b/></w:rPr><w:t>Hanako Example</w:t></w:r></w:p>"
            "</w:body></w:document>"
        ).encode()
        styles = b"<w:styles xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'/>"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", document)
            archive.writestr("word/styles.xml", styles)

    def make_mixed_docx(self, path: Path) -> None:
        document = (
            "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
            "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
            "<w:body>"
            "<w:p><w:r><w:t>First Person</w:t></w:r></w:p>"
            "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Table Email</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
            "<w:p><w:r><w:t>Last Phone</w:t></w:r></w:p>"
            "</w:body></w:document>"
        ).encode()
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", document)

    def test_xlsx_rebuild_preserves_package_and_replaces_cells(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, normalized, output = root / "source.xlsx", root / "normalized.md", root / "rebuilt.xlsx"
            self.make_xlsx(source)
            normalized.write_text("## Sheet: Customers\n- A1={humannameA} | B1={mailaddressA}\n", encoding="utf-8")
            process, result = self.run_cli(SCRIPT, "rebuild", "--input", str(source), "--normalized", str(normalized), "--output", str(output))
            self.assertEqual(process.returncode, 0, result)
            self.assertEqual(result["status"], "rebuilt")
            with zipfile.ZipFile(output) as archive:
                sheet = archive.read("xl/worksheets/sheet1.xml").decode()
                self.assertIn("{humannameA}", sheet)
                self.assertIn("{mailaddressA}", sheet)
                self.assertIn("xl/styles.xml", archive.namelist())

    def test_docx_rebuild_preserves_style_part_and_text(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, normalized, output = root / "source.docx", root / "normalized.md", root / "rebuilt.docx"
            self.make_docx(source)
            normalized.write_text("## Paragraph 1\n{humannameA}\n", encoding="utf-8")
            process, result = self.run_cli(SCRIPT, "rebuild", "--input", str(source), "--normalized", str(normalized), "--output", str(output))
            self.assertEqual(process.returncode, 0)
            self.assertEqual(result["status"], "rebuilt")
            with zipfile.ZipFile(output) as archive:
                document = archive.read("word/document.xml").decode()
                self.assertIn("{humannameA}", document)
                self.assertNotIn("Hanako Example", document)
                self.assertIn("word/styles.xml", archive.namelist())

    def test_rebuilt_xlsx_can_be_reextracted(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, normalized, output, reextract = root / "source.xlsx", root / "normalized.md", root / "rebuilt.xlsx", root / "reextracted.md"
            self.make_xlsx(source)
            normalized.write_text("## Sheet: Customers\n- A1={humannameA} | B1={mailaddressA}\n", encoding="utf-8")
            self.run_cli(SCRIPT, "rebuild", "--input", str(source), "--normalized", str(normalized), "--output", str(output))
            process, result = self.run_cli(ADAPTER, "extract", "--input", str(output), "--output", str(reextract))
            self.assertEqual(process.returncode, 0)
            self.assertEqual(result["status"], "extracted")
            self.assertIn("{humannameA}", reextract.read_text(encoding="utf-8"))

    def test_mixed_docx_preserves_paragraph_table_paragraph_order(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, normalized, output = root / "source.docx", root / "normalized.md", root / "rebuilt.docx"
            self.make_mixed_docx(source)
            extracted = root / "extracted.md"
            process, result = self.run_cli(ADAPTER, "extract", "--input", str(source), "--output", str(extracted))
            self.assertEqual(process.returncode, 0)
            self.assertEqual(result["unit_count"], 3)
            normalized.write_text(
                "## Paragraph 1\n{humannameA}\n\n## Table 1\n{mailaddressA}\n\n## Paragraph 2\n{phoneA}\n",
                encoding="utf-8")
            process, result = self.run_cli(SCRIPT, "rebuild", "--input", str(source), "--normalized", str(normalized), "--output", str(output))
            self.assertEqual(process.returncode, 0, result)
            with zipfile.ZipFile(output) as archive:
                document = archive.read("word/document.xml").decode()
            self.assertLess(document.index("{humannameA}"), document.index("{mailaddressA}"))
            self.assertLess(document.index("{mailaddressA}"), document.index("{phoneA}"))


if __name__ == "__main__":
    unittest.main()
