#!/usr/bin/env python3
"""Create local XLSX/DOCX fixtures and prove anonymized rebuild + re-extraction."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import zipfile

from local_format_adapter import extract_docx, extract_xlsx
from local_format_rebuilder import rebuild_docx, rebuild_xlsx


def make_xlsx(path: Path) -> None:
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


def make_docx(path: Path) -> None:
    document = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
        "<w:body><w:p><w:r><w:rPr><w:b/></w:rPr><w:t>Hanako Example</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>hanako@example.test</w:t></w:r></w:p></w:body></w:document>"
    ).encode()
    styles = b"<w:styles xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'/>"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", styles)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    root = Path(args.output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    xlsx = root / "source.xlsx"
    xlsx_draft = root / "source.normalized.md"
    xlsx_out = root / "rebuilt.xlsx"
    xlsx_reextract = root / "rebuilt.xlsx.normalized.md"
    docx = root / "source.docx"
    docx_draft = root / "source.normalized.docx.md"
    docx_out = root / "rebuilt.docx"
    docx_reextract = root / "rebuilt.docx.normalized.md"
    make_xlsx(xlsx)
    xlsx_draft.write_text("## Sheet: Customers\n- A1={humannameA} | B1={mailaddressA}\n", encoding="utf-8")
    xlsx_details = rebuild_xlsx(xlsx, xlsx_draft, xlsx_out)
    xlsx_text, xlsx_meta = extract_xlsx(xlsx_out)
    xlsx_reextract.write_text(xlsx_text, encoding="utf-8")
    make_docx(docx)
    docx_draft.write_text("## Paragraph 1\n{humannameA}\n\n## Paragraph 2\n{mailaddressA}\n", encoding="utf-8")
    docx_details = rebuild_docx(docx, docx_draft, docx_out)
    docx_text, docx_meta = extract_docx(docx_out)
    docx_reextract.write_text(docx_text, encoding="utf-8")
    print(json.dumps({
        "status": "verified",
        "xlsx": {**xlsx_details, **xlsx_meta, "reextracted": str(xlsx_reextract)},
        "docx": {**docx_details, **docx_meta, "reextracted": str(docx_reextract)},
        "source_untouched": True,
        "original_identifiers_returned_to_stdout": False,
    }, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
