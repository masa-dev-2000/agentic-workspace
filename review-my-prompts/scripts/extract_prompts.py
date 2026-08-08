#!/usr/bin/env python3
"""トランスクリプトから、人が手で書いたプロンプトだけを取り出す。

ツール結果・システム注入・スラッシュコマンド展開・compact要約は除く。
評価対象は「その人がClaudeに向けて実際に打った文」だけ。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

# 人が書いていないもの。1つでも当たれば捨てる。
DROP_PATTERNS = [
    re.compile(r"^\s*<(command-name|command-message|command-args|local-command)"),
    re.compile(r"^\s*<system-reminder"),
    re.compile(r"^\s*<bash-(input|stdout|stderr)"),
    re.compile(r"^\s*\[SYSTEM NOTIFICATION"),
    re.compile(r"<task-notification>"),
    re.compile(r"^\s*<user-prompt-submit-hook"),
    re.compile(r"^\s*Caveat: The messages below"),
    re.compile(r"^\s*This session is being continued from"),
    re.compile(r"^\s*\[Request interrupted"),
    re.compile(r"^\s*API Error"),
]


def text_of(message) -> str | None:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_result":
            return None  # ツール結果は人の発話ではない
        if block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts) if parts else None


def is_human(text: str) -> bool:
    if not text or not text.strip():
        return False
    return not any(p.search(text) for p in DROP_PATTERNS)


def strip_noise(text: str) -> str:
    # 末尾に付く system-reminder ブロックだけ落とす(本文は人が書いている)
    return re.sub(r"<system-reminder>.*?</system-reminder>", "", text, flags=re.S).strip()


def find_session(session: str) -> str:
    base = os.path.join(os.path.expanduser("~"), ".claude", "projects")
    hits = glob.glob(os.path.join(base, "*", f"{session}.jsonl"))
    if not hits:
        sys.exit(f"セッションが見つかりません: {session}")
    return max(hits, key=os.path.getsize)


def main() -> None:
    ap = argparse.ArgumentParser(description="人が書いたプロンプトを抽出する")
    ap.add_argument("session", help="セッションID(jsonl のファイル名)")
    ap.add_argument("--last", type=int, help="末尾N件だけ出す")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass

    prompts = []
    with open(find_session(args.session), encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("type") != "user" or rec.get("isMeta") or rec.get("isCompactSummary"):
                continue
            msg = rec.get("message")
            if not isinstance(msg, dict):
                continue
            text = text_of(msg)
            if text is None or not is_human(text):
                continue
            text = strip_noise(text)
            if text:
                prompts.append({"at": rec.get("timestamp"), "text": text})

    if args.last:
        prompts = prompts[-args.last:]

    if args.json:
        print(json.dumps(prompts, ensure_ascii=False, indent=2))
        return

    print(f"人が書いたプロンプト: {len(prompts)} 件\n")
    for i, p in enumerate(prompts, 1):
        print(f"--- #{i}  ({p['at']})  {len(p['text'])}字")
        print(p["text"])
        print()


if __name__ == "__main__":
    main()
