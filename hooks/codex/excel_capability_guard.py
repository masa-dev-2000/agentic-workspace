from __future__ import annotations

import json
import re
import sys


EXCEL_PATTERN = re.compile(
    r"(?:\bexcel\b|エクセル|(?:\.)?xlsx?|\bworkbook\b|ワークブック|"
    r"スプレッドシート|表計算)",
    re.IGNORECASE,
)

CONTEXT = """Excel capability guard:
- Do not conclude that Excel work is unavailable merely because no live Excel session is connected.
- First discover connected document sessions and Excel-specific tools.
- If live control is unavailable, use the local spreadsheet skill to read, create, edit, and verify .xlsx/.xls/.csv/.tsv artifacts.
- If an Excel-app-only state is required, ask only for the minimum reconnection action while completing all independent analysis, formulas, table design, conversion, and artifact preparation first.
- Try connected Excel, then local workbook editing, then Excel-compatible artifact generation. Report only the exact blocked operation, never that all Excel work is impossible."""


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        prompt = next(
            (
                payload.get(key)
                for key in ("prompt", "user_prompt", "message")
                if isinstance(payload.get(key), str)
            ),
            "",
        )
        if not prompt or len(prompt) > 12000 or not EXCEL_PATTERN.search(prompt):
            return 0
        print(
            json.dumps(
                {
                    "continue": True,
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "additionalContext": CONTEXT,
                    },
                },
                ensure_ascii=False,
            )
        )
    except Exception:
        # This UX guard must never block the user's prompt.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
