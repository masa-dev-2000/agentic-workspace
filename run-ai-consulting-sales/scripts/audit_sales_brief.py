#!/usr/bin/env python3
"""Audit whether an AI consulting sales brief is commercially complete."""

from __future__ import annotations

import argparse
import pathlib
import re
import sys


REQUIRED = {
    "結論": ("結論", "推奨"),
    "顧客の目的": ("顧客のやりたいこと", "顧客の目的", "事業成果"),
    "事実": ("事実",),
    "仮説": ("仮説", "推論"),
    "フェーズ": ("フェーズ", "段階"),
    "初回スコープ": ("初月スコープ", "初回スコープ", "対象業務"),
    "対象外": ("対象外", "除外"),
    "工数": ("工数", "稼働"),
    "期間": ("期間", "納期"),
    "金額": ("金額", "価格", "費用"),
    "見積根拠": ("見積根拠", "価格根拠"),
    "顧客の協力": ("顧客の協力", "顧客側", "相手の時間"),
    "PoC": ("poc", "技術検証", "実証"),
    "意思決定": ("意思決定", "決定事項", "本日のゴール"),
    "次のアクション": ("次のアクション", "次の対応", "next action"),
}


def has_term(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(term.casefold() in lowered for term in terms)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("brief", type=pathlib.Path)
    parser.add_argument("--require", action="append", default=[])
    parser.add_argument("--forbid", action="append", default=[])
    args = parser.parse_args()

    text = args.brief.read_text(encoding="utf-8-sig")
    missing = [label for label, terms in REQUIRED.items() if not has_term(text, terms)]
    missing.extend(term for term in args.require if not has_term(text, (term,)))
    forbidden = [term for term in args.forbid if has_term(text, (term,))]

    vague_promises = []
    if re.search(r"\b\d{1,2}\s*%\s*(?:完成|実装|でき)", text):
        vague_promises.append("根拠のない完成率表現")

    if missing:
        print("MISSING:")
        for item in dict.fromkeys(missing):
            print(f"- {item}")
    if forbidden:
        print("FORBIDDEN:")
        for item in forbidden:
            print(f"- {item}")
    if vague_promises:
        print("WARN:")
        for item in vague_promises:
            print(f"- {item}")

    if missing or forbidden:
        return 1

    print("PASS: commercial decision fields are present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
