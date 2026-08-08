#!/usr/bin/env python3
"""Create ten synthetic, non-production files for local anonymization tests."""

from __future__ import annotations

import json
from pathlib import Path
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: create_batch_fixtures.py OUTPUT_DIR", file=sys.stderr)
        return 2
    output = Path(sys.argv[1]).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    rows = [
        "Customer: Alice Example\nEmail: alice@example.test\nPhone: +81-90-0000-0001\n",
        "担当者: Bob Sample\nメール: bob@example.test\n住所: 東京都千代田区テスト1-1\n",
        "Organization: Example Foods Ltd.\nContact: Carol Demo\nID: CUST-0003\n",
        "氏名: David Fixture\n連絡先: david@example.test\nメモ: synthetic only\n",
        "Account owner: Eva Mock\nAccount: ACCT-0005\nPhone: 03-0000-0005\n",
        "会社名: Sample Works\n代表: Frank Test\nURL: https://example.test/contact\n",
        "Name: Grace Placeholder\nEmail: grace@example.test\n住所: 大阪府大阪市テスト2-2\n",
        "顧客: Henry Synthetic\n顧客ID: CUST-0008\nメール: henry@example.test\n",
        "Vendor: Iota Demo\n担当者: Irene Example\n電話: +1-202-555-0109\n",
        "Project lead: Jack Fixture\nEmail: jack@example.test\n秘密: synthetic-secret-10\n",
    ]
    for index, text in enumerate(rows, start=1):
        (output / f"case-{index:02d}.md").write_text(text, encoding="utf-8", newline="\n")
    print(json.dumps({"status": "created", "file_count": len(rows), "output_dir": str(output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
