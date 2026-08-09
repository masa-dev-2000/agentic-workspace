from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from telemetry_store import TelemetryStore


EVENTS = ("PostToolUse", "UserPromptSubmit", "Stop")
OPTIMIZER_TASK = "Codex Skill Telemetry Optimizer"


def hooks_path() -> Path:
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    return codex_home / "hooks.json"


def command() -> str:
    script = Path(__file__).with_name("capture_hook.py").resolve()
    return f'python -X utf8 "{script}"'


def is_ours(value: object) -> bool:
    return isinstance(value, str) and "skill-telemetry" in value and "capture_hook.py" in value


def load(path: Path) -> dict:
    if not path.exists():
        return {"hooks": {}}
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    return value if isinstance(value, dict) else {"hooks": {}}


def installed(config: dict, event: str) -> bool:
    for group in config.get("hooks", {}).get(event, []):
        for hook in group.get("hooks", []):
            if is_ours(hook.get("command")) or is_ours(hook.get("commandWindows")):
                return True
    return False


def install(path: Path) -> dict:
    # Provision the stable pseudonym key and schema before enabling the
    # spool-only Hook. Hooks deliberately never bootstrap domain state.
    telemetry = TelemetryStore(root=path.parent / "skill-telemetry")
    config = load(path)
    config.setdefault("hooks", {})
    config["description"] = "Collect local failure, feedback, AI Project Manager inbox, and Skill telemetry evidence."
    for event in EVENTS:
        groups = config["hooks"].setdefault(event, [])
        if installed(config, event):
            if event == "PostToolUse":
                for existing_group in groups:
                    if any(
                        is_ours(hook.get("command")) or is_ours(hook.get("commandWindows"))
                        for hook in existing_group.get("hooks", [])
                    ):
                        existing_group["matcher"] = "*"
            continue
        hook = {
            "type": "command",
            "command": command(),
            "commandWindows": command(),
            "timeout": 2,
        }
        if event == "Stop":
            hook["statusMessage"] = "Saving local Skill telemetry"
        group = {"hooks": [hook]}
        if event == "PostToolUse":
            group["matcher"] = "*"
        groups.append(group)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_suffix(".json.skill-telemetry.bak")
        shutil.copy2(path, backup)
    temp = path.with_suffix(f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)
    return {
        "initialized": telemetry.status().get("initialized", False),
        "installed": {event: installed(config, event) for event in EVENTS},
        "path": str(path),
    }


def optimizer_command() -> str:
    cli = Path(__file__).with_name("telemetry_cli.py").resolve()
    return f'"{sys.executable}" -X utf8 "{cli}" auto-optimize'


def scheduled_task(action: str) -> dict:
    if os.name != "nt":
        return {"ok": False, "error": "Windows Task Scheduler is unavailable"}
    if action == "install":
        args = [
            "schtasks.exe", "/Create", "/TN", OPTIMIZER_TASK,
            "/TR", optimizer_command(), "/SC", "MINUTE", "/MO", "15", "/F",
        ]
    elif action == "remove":
        args = ["schtasks.exe", "/Delete", "/TN", OPTIMIZER_TASK, "/F"]
    else:
        args = ["schtasks.exe", "/Query", "/TN", OPTIMIZER_TASK, "/FO", "LIST"]
    completed = subprocess.run(args, capture_output=True, text=True, encoding="oem", errors="replace", check=False)
    return {
        "ok": completed.returncode == 0,
        "task": OPTIMIZER_TASK,
        "action": action,
        "stdout": completed.stdout[-2000:],
        "stderr": completed.stderr[-2000:],
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=("status", "install", "schedule-status", "schedule-install", "schedule-remove"))
    args = p.parse_args()
    path = hooks_path()
    if args.command == "install":
        result = install(path)
    elif args.command.startswith("schedule-"):
        result = scheduled_task(args.command.removeprefix("schedule-"))
    else:
        config = load(path)
        result = {"installed": {event: installed(config, event) for event in EVENTS}, "path": str(path)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
