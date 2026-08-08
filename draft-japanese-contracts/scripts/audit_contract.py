#!/usr/bin/env python3
import argparse
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def extract_docx(path):
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    root = ET.fromstring(xml)
    paragraphs = []
    table_rows = []
    for paragraph in root.iter(W + "p"):
        text = "".join(node.text or "" for node in paragraph.iter(W + "t"))
        if text:
            paragraphs.append(text)
    for table in root.iter(W + "tbl"):
        for row in table.findall(W + "tr"):
            values = []
            for cell in row.findall(W + "tc"):
                values.append("".join(node.text or "" for node in cell.iter(W + "t")).strip())
            table_rows.append(values)
    return "\n".join(paragraphs), table_rows


def extract(path):
    if path.suffix.lower() == ".docx":
        return extract_docx(path)
    return path.read_text(encoding="utf-8"), []


def main():
    parser = argparse.ArgumentParser(description="Audit Japanese contract text and DOCX tables.")
    parser.add_argument("contract", type=Path)
    parser.add_argument("--require", action="append", default=[])
    parser.add_argument("--forbid", action="append", default=[])
    parser.add_argument("--check-empty-table-values", action="store_true")
    args = parser.parse_args()

    text, table_rows = extract(args.contract)
    failures = []

    for phrase in args.require:
        if phrase not in text:
            failures.append(f"missing required phrase: {phrase}")
    for phrase in args.forbid:
        if phrase in text:
            failures.append(f"forbidden phrase remains: {phrase}")

    contradictions = [
        ("自動更新しない", "自動更新され"),
        ("後払い", "開始日前までに支払う"),
    ]
    for left, right in contradictions:
        if left in text and right in text:
            failures.append(f"possible contradiction: {left} / {right}")

    if args.check_empty_table_values:
        for index, row in enumerate(table_rows, 1):
            if len(row) == 2 and row[0] and not row[1]:
                failures.append(f"empty table value at row {index}: {row[0]}")
            if len(row) == 2 and row[0] and re.fullmatch(r"[　\\s＿_]*", row[1] or ""):
                failures.append(f"placeholder-only table value at row {index}: {row[0]}")
            if len(row) == 2 and row[0] and re.fullmatch(
                r"[　\\s＿_]*年[　\\s＿_]*月[　\\s＿_]*日", row[1] or ""
            ):
                failures.append(f"unresolved date value at row {index}: {row[0]}")
            for cell in row:
                if re.fullmatch(r"(所在地|名称|代表者|署名／押印)：[　\\s＿_]*", cell or ""):
                    failures.append(f"unresolved signature field at row {index}: {cell}")

        if re.search(r"契約締結日：[　\\s＿_]*年[　\\s＿_]*月[　\\s＿_]*日", text):
            failures.append("unresolved contract signing date")

    developer_terms = ["締結前必須", "開発者", "TODO", "Claude", "ChatGPT", "Codex", "Make"]
    for term in developer_terms:
        if term in text:
            failures.append(f"developer/tool term found: {term}")

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PASS")
    print(f"- characters: {len(text)}")
    print(f"- tables: {len(table_rows)} rows")
    print(f"- required phrases: {len(args.require)}")
    print(f"- forbidden phrases: {len(args.forbid)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
