#!/usr/bin/env python3
"""Extract supported binary documents to local UTF-8 Markdown without printing content."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from path_guard import safe_path
import zipfile
import xml.etree.ElementTree as ET


SUPPORTED = {".xlsx", ".docx", ".pdf"}
MAX_BYTES = 50 * 1024 * 1024


class AdapterFailure(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def xml_text(node: ET.Element) -> str:
    return "".join(node.itertext()).strip()


def extract_xlsx(source: Path) -> tuple[str, dict]:
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
    rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    try:
        with zipfile.ZipFile(source) as archive:
            names = set(archive.namelist())
            shared: list[str] = []
            if "xl/sharedStrings.xml" in names:
                root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                shared = [xml_text(item) for item in root.findall("m:si", ns)]
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            targets = {
                rel.attrib["Id"]: rel.attrib["Target"]
                for rel in rels.findall(f"{{{rel_ns}}}Relationship")
            }
            sections: list[str] = []
            sheet_count = 0
            cell_count = 0
            for sheet in workbook.findall("m:sheets/m:sheet", ns):
                sheet_count += 1
                title = sheet.attrib.get("name", f"Sheet{sheet_count}")
                target = targets.get(sheet.attrib.get(f"{{{ns['r']}}}id"), "")
                part = "xl/" + target.lstrip("/")
                if part not in names:
                    continue
                root = ET.fromstring(archive.read(part))
                lines = [f"## Sheet: {title}"]
                for row in root.findall("m:sheetData/m:row", ns):
                    values: list[str] = []
                    for cell in row.findall("m:c", ns):
                        value = cell.find("m:v", ns)
                        text = "" if value is None else value.text or ""
                        if cell.attrib.get("t") == "s" and text.isdigit():
                            index = int(text)
                            text = shared[index] if index < len(shared) else text
                        values.append(f"{cell.attrib.get('r', '?')}={text}")
                        cell_count += 1
                    if values:
                        lines.append("- " + " | ".join(values))
                sections.append("\n".join(lines))
            return "\n\n".join(sections) + "\n", {
                "format": "xlsx", "sheet_count": sheet_count, "unit_count": cell_count,
            }
    except (KeyError, ET.ParseError, zipfile.BadZipFile):
        raise AdapterFailure("structured_format_invalid")


def extract_docx(source: Path) -> tuple[str, dict]:
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    try:
        with zipfile.ZipFile(source) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
    except (KeyError, ET.ParseError, zipfile.BadZipFile):
        raise AdapterFailure("structured_format_invalid")
    blocks: list[str] = []
    paragraph_count = 0
    table_count = 0
    body = root.find(".//w:body", ns)
    for element in list(body or []):
        if element.tag == f"{{{ns['w']}}}p":
            text = "".join(node.text or "" for node in element.findall(".//w:t", ns)).strip()
            if text:
                paragraph_count += 1
                blocks.append(f"## Paragraph {paragraph_count}\n{text}")
            continue
        if element.tag != f"{{{ns['w']}}}tbl":
            continue
        table = element
        table_count += 1
        rows = []
        for row in table.findall("w:tr", ns):
            cells = []
            for cell in row.findall("w:tc", ns):
                cells.append(" ".join(
                    ("".join(node.text or "" for node in p.findall(".//w:t", ns))).strip()
                    for p in cell.findall(".//w:p", ns)
                ).strip())
            if cells:
                rows.append(" | ".join(cells))
        if rows:
            blocks.append(f"## Table {table_count}\n" + "\n".join(rows))
    return "\n\n".join(blocks) + "\n", {
        "format": "docx", "paragraph_count": paragraph_count,
        "table_count": table_count, "unit_count": paragraph_count + table_count,
    }


def extract_pdf(source: Path) -> tuple[str, dict]:
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        PdfReader = None  # type: ignore
    if PdfReader is not None:
        try:
            reader = PdfReader(str(source))
            pages: list[str] = []
            for index, page in enumerate(reader.pages, 1):
                text = (page.extract_text() or "").strip()
                pages.append(f"## Page {index}\n{text}")
            return "\n\n".join(pages) + "\n", {
                "format": "pdf", "page_count": len(reader.pages),
                "unit_count": len(reader.pages), "extractor": "pypdf",
            }
        except Exception:
            raise AdapterFailure("pdf_extract_failed")
    executable = shutil.which("pdftotext")
    if not executable:
        raise AdapterFailure("missing_dependency_pdf_extractor")
    try:
        with tempfile.TemporaryDirectory(prefix="local-pdf-") as folder:
            target = Path(folder, "extract.txt")
            result = subprocess.run(
                [executable, "-layout", str(source), str(target)],
                capture_output=True, text=True, check=False,
            )
            if result.returncode != 0 or not target.is_file():
                raise AdapterFailure("pdf_extract_failed")
            text = target.read_text(encoding="utf-8", errors="replace")
        pages = text.split("\f")
        pages = [page.strip() for page in pages if page.strip()]
        return "\n\n".join(
            f"## Page {index}\n{page}" for index, page in enumerate(pages, 1)
        ) + "\n", {
            "format": "pdf", "page_count": len(pages),
            "unit_count": len(pages), "extractor": "pdftotext",
        }
    except AdapterFailure:
        raise
    except Exception:
        raise AdapterFailure("pdf_extract_failed")


def extract(source: Path) -> tuple[str, dict]:
    extension = source.suffix.lower()
    if extension == ".xlsx":
        return extract_xlsx(source)
    if extension == ".docx":
        return extract_docx(source)
    if extension == ".pdf":
        return extract_pdf(source)
    raise AdapterFailure("unsupported_input")


def main() -> int:
    parser = argparse.ArgumentParser(description="Local binary-document adapter for anonymization")
    parser.add_argument("extract", choices=["extract"])
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        source = safe_path(args.input, must_exist=True)
        output = safe_path(args.output, output=True)
        if source.suffix.lower() not in SUPPORTED:
            raise AdapterFailure("unsupported_input")
        if source.stat().st_size > MAX_BYTES:
            raise AdapterFailure("file_too_large")
        content, details = extract(source)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8", newline="\n")
        emit({
            "status": "extracted", "source_extension": source.suffix.lower(),
            "output_path": str(output), "source_sha256": sha256(source), **details,
        })
        return 0
    except AdapterFailure as error:
        emit({"status": "failed", "error_code": error.code})
        return 2
    except Exception:
        emit({"status": "failed", "error_code": "adapter_failed"})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
