"""SessionEnd hook: enqueue the naming event, then process it immediately.

Replaces the per-minute 'OpenAI-Codex-Session-Naming-Worker' scheduled task
(disabled 2026-08-01). The router is spawned detached with CREATE_NO_WINDOW
so no console window flashes; its process lock makes concurrent runs safe.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

CODEX_HOME = Path.home() / ".codex"
SKILLS = CODEX_HOME / "skills"

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


def main() -> int:
    sys.path.insert(0, str(SKILLS / "name-work-sessions" / "scripts"))
    import session_end_hook

    raw = sys.stdin.buffer.read(session_end_hook.MAX_INPUT_BYTES + 1)
    try:
        session_end_hook.handle(raw)
    except BaseException:
        pass

    try:
        subprocess.Popen(
            [
                sys.executable,
                "-X",
                "utf8",
                str(SKILLS / ".adaptive-system" / "runtime" / "batch_router.py"),
                "--state",
                str(CODEX_HOME / "name-work-sessions"),
                "--config",
                str(SKILLS / "name-work-sessions" / "runtime" / "router-config.json"),
                "--execute-adapters",
            ],
            cwd=str(SKILLS),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
        )
    except BaseException:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
