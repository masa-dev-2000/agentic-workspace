from __future__ import annotations

import argparse
import html
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence


TASK_NAME = "OpenAI-Codex-Session-Naming-Worker"
TASK_MARKER = "OpenAI Codex name-work-sessions managed worker v1"
HOOK_TIMEOUT_SECONDS = 3
TASK_TIMEOUT_SECONDS = 300
TASK_INTERVAL = "PT1M"
MAX_STATUS_FILE_BYTES = 64 * 1024


def _skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _skills_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()


def _hooks_path() -> Path:
    return _codex_home() / "hooks.json"


def _state_root() -> Path:
    configured = os.environ.get("CODEX_SESSION_NAMING_HOME")
    if configured:
        return Path(configured).expanduser()
    return _codex_home() / "name-work-sessions"


def _hook_command(event_name: str = "SessionEnd") -> str:
    script = _skill_root() / "scripts" / "session_end_hook.py"
    python = Path(sys.executable).resolve()
    command = f'"{python}" -X utf8 "{script}"'
    if event_name == "SessionStart":
        command += ' --event SessionStart --require-source resume'
    return command


def _hook_definition(event_name: str = "SessionEnd") -> dict[str, Any]:
    if event_name not in {"SessionEnd", "SessionStart"}:
        raise ValueError("unsupported-hook-event")
    command = _hook_command(event_name)
    matcher = "other" if event_name == "SessionEnd" else "resume"
    status_message = (
        "Queueing automatic session name"
        if event_name == "SessionEnd"
        else "Checking resumed session name"
    )
    return {
        "matcher": matcher,
        "hooks": [
            {
                "type": "command",
                "command": command,
                "commandWindows": command,
                "timeout": HOOK_TIMEOUT_SECONDS,
                "statusMessage": status_message,
            }
        ],
    }


def _is_our_hook(entry: Any) -> bool:
    if not isinstance(entry, Mapping):
        return False
    hooks = entry.get("hooks")
    if not isinstance(hooks, list):
        return False
    expected = str(_skill_root() / "scripts" / "session_end_hook.py").lower()
    for hook in hooks:
        if not isinstance(hook, Mapping):
            continue
        for key in ("command", "commandWindows"):
            value = hook.get(key)
            if isinstance(value, str) and expected in value.lower():
                return True
    return False


def _load_hooks(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "description": "Codex lifecycle hooks",
            "hooks": {},
        }
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("hooks-file-is-not-valid-json") from error
    if not isinstance(value, dict):
        raise RuntimeError("hooks-file-root-must-be-an-object")
    hooks = value.get("hooks")
    if hooks is None:
        value["hooks"] = {}
    elif not isinstance(hooks, dict):
        raise RuntimeError("hooks-property-must-be-an-object")
    return value


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def hook_status(path: Path) -> dict[str, Any]:
    try:
        config = _load_hooks(path)
    except RuntimeError as error:
        return {
            "installed": False,
            "configured": False,
            "valid": False,
            "error": str(error),
        }
    configured: dict[str, bool] = {}
    for event_name in ("SessionEnd", "SessionStart"):
        entries = config["hooks"].get(event_name, [])
        expected = _hook_definition(event_name)
        configured[event_name] = bool(
            isinstance(entries, list) and expected in entries
        )
    all_configured = all(configured.values())
    return {
        "installed": all_configured,
        "configured": all_configured,
        "valid": True,
        "path": str(path),
        "triggers": {
            "sessionEndOther": configured["SessionEnd"],
            "sessionStartResume": configured["SessionStart"],
        },
        "trust": {
            "state": "unknown",
            "verified": False,
            "note": "Hook trust is not inferable from hooks.json; verify it in /hooks.",
        },
    }


def _install_event_hook(config: dict[str, Any], event_name: str) -> bool:
    entries = config["hooks"].get(event_name)
    if entries is None:
        entries = []
        config["hooks"][event_name] = entries
    if not isinstance(entries, list):
        raise RuntimeError(f"{event_name}-hooks-must-be-an-array")
    ours = [index for index, entry in enumerate(entries) if _is_our_hook(entry)]
    changed = False
    if ours:
        first = ours[0]
        expected = _hook_definition(event_name)
        if entries[first] != expected:
            entries[first] = expected
            changed = True
        for index in reversed(ours[1:]):
            del entries[index]
            changed = True
    else:
        entries.append(_hook_definition(event_name))
        changed = True
    return changed


def install_hook(path: Path) -> bool:
    config = _load_hooks(path)
    changed = False
    for event_name in ("SessionEnd", "SessionStart"):
        changed = _install_event_hook(config, event_name) or changed
    if changed:
        _atomic_write_json(path, config)
    return changed


def uninstall_hook(path: Path) -> bool:
    config = _load_hooks(path)
    changed = False
    for event_name in ("SessionEnd", "SessionStart"):
        entries = config["hooks"].get(event_name)
        if not isinstance(entries, list):
            continue
        retained = [entry for entry in entries if not _is_our_hook(entry)]
        if len(retained) == len(entries):
            continue
        changed = True
        if retained:
            config["hooks"][event_name] = retained
        else:
            config["hooks"].pop(event_name, None)
    if changed:
        _atomic_write_json(path, config)
    return changed


def _task_arguments() -> str:
    router = _skills_root() / ".adaptive-system" / "runtime" / "batch_router.py"
    config = _skill_root() / "runtime" / "router-config.json"
    state = _state_root()
    return (
        f'-X utf8 "{router}" --state "{state}" '
        f'--config "{config}" --execute-adapters'
    )


def build_task_xml(start_boundary: datetime | None = None) -> str:
    start = start_boundary or datetime.now().astimezone().replace(tzinfo=None)
    start = (start + timedelta(minutes=1)).replace(microsecond=0)
    python = html.escape(str(Path(sys.executable).resolve()))
    arguments = html.escape(_task_arguments())
    working = html.escape(str(_skills_root()))
    marker = html.escape(TASK_MARKER)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{marker}</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <Repetition>
        <Interval>{TASK_INTERVAL}</Interval>
        <Duration>P1D</Duration>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>{start.isoformat(timespec="seconds")}</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
    <ExecutionTimeLimit>PT5M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{python}</Command>
      <Arguments>{arguments}</Arguments>
      <WorkingDirectory>{working}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _run_schtasks(
    arguments: Sequence[str], *, timeout: float = 15.0
) -> subprocess.CompletedProcess[bytes]:
    if os.name != "nt":
        raise RuntimeError("task-scheduler-is-windows-only")
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.run(
            ["schtasks.exe", *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            shell=False,
            creationflags=creation_flags,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("task-scheduler-command-failed") from error


def _safe_task_text(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError:
            pass
    ascii_projection = raw.replace(b"\x00", b"").decode("ascii", errors="ignore")
    if TASK_MARKER in ascii_projection:
        return ascii_projection
    for encoding in ("utf-8", "cp932"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return ascii_projection


def _task_error(result: subprocess.CompletedProcess[bytes]) -> str:
    text = _safe_task_text(result.stderr or result.stdout)
    text = " ".join(text.split())
    return text[:300] or f"exit-{result.returncode}"


def task_status() -> dict[str, Any]:
    if os.name != "nt":
        return {"installed": False, "supported": False}
    result = _run_schtasks(["/Query", "/TN", TASK_NAME, "/XML"])
    if result.returncode != 0:
        return {"installed": False, "supported": True, "owned": False}
    xml = _safe_task_text(result.stdout)
    return {
        "installed": True,
        "supported": True,
        "owned": TASK_MARKER in xml,
    }


def install_task() -> bool:
    status = task_status()
    if status.get("installed") and not status.get("owned"):
        raise RuntimeError("task-name-is-owned-by-another-program")
    xml = build_task_xml()
    temporary: Path | None = None
    try:
        temporary_dir = _skill_root() / "runtime"
        temporary_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=temporary_dir,
            prefix="codex-session-naming-",
            suffix=".xml",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(xml.encode("utf-16"))
            handle.flush()
            os.fsync(handle.fileno())
        result = _run_schtasks(
            ["/Create", "/TN", TASK_NAME, "/XML", str(temporary), "/F"]
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"task-scheduler-create-failed:{_task_error(result)}"
            )
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    verified = task_status()
    if not verified.get("installed") or not verified.get("owned"):
        raise RuntimeError("task-scheduler-verification-failed")
    return not bool(status.get("installed"))


def uninstall_task() -> bool:
    status = task_status()
    if not status.get("installed"):
        return False
    if not status.get("owned"):
        raise RuntimeError("refusing-to-delete-unowned-task")
    result = _run_schtasks(["/Delete", "/TN", TASK_NAME, "/F"])
    if result.returncode != 0:
        raise RuntimeError(
            f"task-scheduler-delete-failed:{_task_error(result)}"
        )
    return True


def run_task_now() -> bool:
    status = task_status()
    if not status.get("installed") or not status.get("owned"):
        raise RuntimeError("managed-task-is-not-installed")
    result = _run_schtasks(["/Run", "/TN", TASK_NAME])
    if result.returncode != 0:
        raise RuntimeError(f"task-scheduler-run-failed:{_task_error(result)}")
    return True


def _pending_count() -> int:
    try:
        return sum(1 for _ in (_state_root() / "spool").glob("*.json"))
    except OSError:
        return 0


def _observed_events() -> dict[str, Any]:
    observed = {
        "sessionEndOther": False,
        "sessionStartResume": False,
    }
    candidates = [
        *(_state_root() / "spool").glob("*.json"),
        *(_state_root() / "name-receipts").glob("*.json"),
    ]
    try:
        candidates.sort(key=lambda path: path.stat().st_mtime_ns)
    except OSError:
        pass
    for path in candidates[-500:]:
        try:
            if path.stat().st_size > MAX_STATUS_FILE_BYTES:
                continue
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict):
            continue
        event_type = value.get("triggerEventType", value.get("eventType"))
        metadata = value.get("metadata")
        trigger_source = value.get("triggerSource")
        if event_type == "lifecycle.session-end":
            observed["sessionEndOther"] = True
        elif event_type == "lifecycle.session-start" and (
            trigger_source == "resume"
            or (
                isinstance(metadata, dict)
                and metadata.get("status") == "resume"
            )
        ):
            observed["sessionStartResume"] = True
    return {
        **observed,
        "conclusiveWhenFalse": False,
        "basis": "pending body-free events or verified naming receipts",
    }


def _emit(value: Mapping[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Configure automatic SessionEnd and resume fallback naming."
    )
    parser.add_argument(
        "command", choices=("install", "status", "run-now", "uninstall")
    )
    parser.add_argument("--hooks-file", type=Path)
    parser.add_argument(
        "--skip-task",
        action="store_true",
        help="Only for isolated tests or manual hook-file maintenance.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    hooks_path = args.hooks_file or _hooks_path()
    try:
        if args.command == "status":
            _emit(
                {
                    "hook": hook_status(hooks_path),
                    "task": (
                        {"skipped": True}
                        if args.skip_task
                        else task_status()
                    ),
                    "pending": _pending_count(),
                    "observed": _observed_events(),
                    "stateRoot": str(_state_root()),
                }
            )
            return 0
        if args.command == "run-now":
            if args.skip_task:
                raise RuntimeError("run-now-requires-task-scheduler")
            run_task_now()
            _emit({"started": True, "pending": _pending_count()})
            return 0
        if args.command == "uninstall":
            hook_removed = uninstall_hook(hooks_path)
            task_removed = False if args.skip_task else uninstall_task()
            _emit({"hookRemoved": hook_removed, "taskRemoved": task_removed})
            return 0

        task_created = False
        if not args.skip_task:
            task_created = install_task()
        try:
            hook_changed = install_hook(hooks_path)
        except BaseException:
            if task_created:
                try:
                    uninstall_task()
                except RuntimeError:
                    pass
            raise
        _state_root().mkdir(parents=True, exist_ok=True)
        _emit(
            {
                "installed": True,
                "hookChanged": hook_changed,
                "taskCreated": task_created,
                "nextStep": (
                    "Restart Codex, run /hooks, and review/trust the SessionEnd "
                    "and SessionStart(resume) commands."
                ),
            }
        )
        return 0
    except (OSError, RuntimeError) as error:
        _emit({"ok": False, "error": str(error)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
