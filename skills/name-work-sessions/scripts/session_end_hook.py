from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


MAX_INPUT_BYTES = 256 * 1024


def _skills_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _state_root() -> Path:
    configured = os.environ.get("CODEX_SESSION_NAMING_HOME")
    if configured:
        return Path(configured).expanduser()
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    return codex_home / "name-work-sessions"


def _load_dispatcher():
    runtime = _skills_root() / ".adaptive-system" / "runtime"
    sys.path.insert(0, str(runtime))
    try:
        from hook_dispatcher import dispatch_bytes
    finally:
        try:
            sys.path.remove(str(runtime))
        except ValueError:
            pass
    return dispatch_bytes


def handle(
    raw: bytes,
    *,
    state_root: Path | None = None,
    event_name: str = "SessionEnd",
    required_source: str | None = None,
) -> str:
    if len(raw) > MAX_INPUT_BYTES:
        return "input-too-large"
    try:
        if event_name not in {"SessionEnd", "SessionStart"}:
            return "invalid-event-name"
        if required_source is not None:
            try:
                source: Any = json.loads(raw.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return "invalid-input"
            if not isinstance(source, dict):
                return "invalid-input"
            if source.get("source") != required_source:
                return "ignored-source"
            source = dict(source)
            source["status"] = required_source
            raw = json.dumps(
                source,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        dispatch_bytes = _load_dispatcher()
        root = state_root or _state_root()
        result = dispatch_bytes(
            raw,
            root / "spool",
            event_override=event_name,
        )
        return result.status
    except BaseException:
        return "hook-error"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--event",
        choices=("SessionEnd", "SessionStart"),
        default="SessionEnd",
    )
    parser.add_argument("--require-source", choices=("resume",))
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        handle(
            raw,
            event_name=args.event,
            required_source=args.require_source,
        )
    except BaseException:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
