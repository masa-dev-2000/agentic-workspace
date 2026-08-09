from __future__ import annotations

import json
import os
import sys

from telemetry_store import TelemetryStore

MAX_INPUT = 1_000_000


def main() -> int:
    if os.environ.get("CODEX_SKILL_TELEMETRY_ACTIVE") == "1":
        return 0
    os.environ["CODEX_SKILL_TELEMETRY_ACTIVE"] = "1"
    event: dict = {}
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            return 0
        event = json.loads(raw.decode("utf-8"))
        if not isinstance(event, dict):
            return 0
        # Hooks never open or initialize the domain database. They only derive one
        # bounded privacy-safe envelope and atomically append it to the local spool.
        store = TelemetryStore(initialize=False)
        store.spool_hook_event(event)
        if str(event.get("hook_event_name", "")) == "Stop":
            print("{}")
    except Exception:
        # Telemetry must never block or alter the original turn.
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
