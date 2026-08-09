#!/usr/bin/env python3
"""Rebuild XLSX/DOCX by applying local normalized anonymized text to copied OOXML."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import zipfile

from path_guard import safe_path, same_file
import xml.etree.ElementTree as ET


NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS_X = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
ET.register_namespace("w", NS_W)
ET.register_namespace("", NS_X)


class RebuildFailure(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def parse_xlsx_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip().startswith("-"):
            continue
        for segment in line.strip()[1:].split(" | "):
            match = re.match(r"\s*([A-Z]+\d+)=(.*)$", segment)
            if match:
                values[match.group(1)] = match.group(2)
    if not values:
        raise RebuildFailure("normalized_cells_missing")
    return values


def rebuild_xlsx(source: Path, normalized: Path, output: Path) -> dict:
    values = parse_xlsx_values(normalized.read_text(encoding="utf-8"))
    changed = 0
    with zipfile.ZipFile(source) as source_zip:
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target_zip:
            for info in source_zip.infolist():
                data = source_zip.read(info.filename)
                if info.filename.startswith("xl/worksheets/") and info.filename.endswith(".xml"):
                    try:
                        root = ET.fromstring(data)
                    except ET.ParseError:
                        raise RebuildFailure("xlsx_sheet_invalid")
                    for cell in root.findall(f".//{{{NS_X}}}c"):
                        ref = cell.attrib.get("r")
                        if not ref or ref not in values or cell.find(f"{{{NS_X}}}f") is not None:
                            continue
                        for child in list(cell):
                            if child.tag in {f"{{{NS_X}}}v", f"{{{NS_X}}}is"}:
                                cell.remove(child)
                        value = ET.SubElement(cell, f"{{{NS_X}}}v")
                        value.text = values[ref]
                        cell.set("t", "str")
                        changed += 1
                    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                target_zip.writestr(info, data)
    if changed == 0:
        raise RebuildFailure("xlsx_no_cells_rebuilt")
    return {"format": "xlsx", "cells_rebuilt": changed}


def parse_docx_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current:
                blocks.append("\n".join(current).strip())
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current).strip())
    blocks = [block for block in blocks if block]
    if not blocks:
        raise RebuildFailure("normalized_blocks_missing")
    return blocks


def text_nodes(element: ET.Element) -> list[ET.Element]:
    return element.findall(f".//{{{NS_W}}}t")


def replace_element_text(element: ET.Element, value: str) -> bool:
    nodes = text_nodes(element)
    if not nodes:
        return False
    nodes[0].text = value
    for node in nodes[1:]:
        node.text = ""
    return True


def replace_table_text(table: ET.Element, value: str) -> bool:
    rows = [line for line in value.splitlines() if line.strip()]
    table_rows = table.findall(f"{{{NS_W}}}tr")
    changed = False
    for row, line in zip(table_rows, rows):
        cells = [cell.strip() for cell in line.split("|")]
        table_cells = row.findall(f"{{{NS_W}}}tc")
        for cell, replacement in zip(table_cells, cells):
            changed = replace_element_text(cell, replacement) or changed
    return changed


def rebuild_docx(source: Path, normalized: Path, output: Path) -> dict:
    blocks = parse_docx_blocks(normalized.read_text(encoding="utf-8"))
    changed = 0
    with zipfile.ZipFile(source) as source_zip:
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target_zip:
            for info in source_zip.infolist():
                data = source_zip.read(info.filename)
                if info.filename == "word/document.xml":
                    try:
                        root = ET.fromstring(data)
                    except ET.ParseError:
                        raise RebuildFailure("docx_document_invalid")
                    body = root.find(f".//{{{NS_W}}}body")
                    elements = list(body) if body is not None else []
                    elements = [
                        element for element in elements
                        if element.tag in {f"{{{NS_W}}}p", f"{{{NS_W}}}tbl"}
                    ]
                    for element, value in zip(elements, blocks):
                        changed_element = (
                            replace_table_text(element, value)
                            if element.tag == f"{{{NS_W}}}tbl"
                            else replace_element_text(element, value)
                        )
                        if changed_element:
                            changed += 1
                    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                target_zip.writestr(info, data)
    if changed == 0:
        raise RebuildFailure("docx_no_blocks_rebuilt")
    return {"format": "docx", "blocks_rebuilt": changed}


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild an anonymized XLSX or DOCX locally")
    parser.add_argument("rebuild", choices=["rebuild"])
    parser.add_argument("--input", required=True)
    parser.add_argument("--normalized", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        source = safe_path(args.input, must_exist=True)
        normalized = safe_path(args.normalized, must_exist=True)
        output = safe_path(args.output, output=True)
        if output == source or output == normalized or same_file(output, source) or same_file(output, normalized):
            raise RebuildFailure("source_overwrite_forbidden")
        extension = source.suffix.lower()
        if extension not in {".xlsx", ".docx"}:
            raise RebuildFailure("unsupported_input")
        output.parent.mkdir(parents=True, exist_ok=True)
        details = rebuild_xlsx(source, normalized, output) if extension == ".xlsx" else rebuild_docx(source, normalized, output)
        emit({"status": "rebuilt", "source_path": str(source), "output_path": str(output),
              "source_sha256": digest(source), "normalized_sha256": digest(normalized),
              "output_sha256": digest(output), **details})
        return 0
    except RebuildFailure as error:
        emit({"status": "failed", "error_code": error.code})
        return 2
    except Exception:
        emit({"status": "failed", "error_code": "rebuild_failed"})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
