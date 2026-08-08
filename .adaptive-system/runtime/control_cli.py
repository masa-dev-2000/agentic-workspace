from __future__ import annotations

import argparse
import importlib.util
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from process_lock import LocalProcessLock


UTC = timezone.utc


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _atomic_json(path: Path, value: Any) -> None:
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
            json.dump(
                value, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default


def _queue_count(path: Path) -> int:
    try:
        return sum(1 for candidate in path.glob("*.json") if candidate.is_file())
    except OSError:
        return 0


class _ControlMutationLock:
    def __init__(self, path: Path, timeout_seconds: float = 2.0) -> None:
        self.path = path
        self.process_lock = LocalProcessLock(
            path,
            timeout_seconds=timeout_seconds,
        )

    def __enter__(self) -> "_ControlMutationLock":
        if not self.process_lock.acquire():
            raise RuntimeError("control-lock-timeout")
        return self

    def __exit__(self, *_: object) -> None:
        self.process_lock.release()


class ControlSurface:
    def __init__(self, state_dir: Path | str) -> None:
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.control_path = self.state_dir / "control.json"

    @staticmethod
    def _default_control() -> dict[str, Any]:
        return {
            "schemaVersion": "1.0",
            "revision": 0,
            "paused": False,
            "pauseReason": None,
            "killSwitch": {
                "enabled": False,
                "reason": None,
                "changedAt": None,
            },
            "updatedAt": None,
        }

    def _load(self) -> dict[str, Any]:
        if not self.control_path.exists():
            return self._default_control()
        value = _read_json(self.control_path, None)
        if not isinstance(value, dict):
            raise ValueError("invalid-control")
        default = self._default_control()
        default.update(
            {
                key: value[key]
                for key in ("revision", "paused", "pauseReason", "updatedAt")
                if key in value
            }
        )
        kill_switch = value.get("killSwitch")
        if isinstance(kill_switch, dict):
            default["killSwitch"].update(
                {
                    key: kill_switch[key]
                    for key in ("enabled", "reason", "changedAt")
                    if key in kill_switch
                }
            )
        default["paused"] = default["paused"] is True
        default["killSwitch"]["enabled"] = default["killSwitch"]["enabled"] is True
        if (
            value.get("schemaVersion") != "1.0"
            or not isinstance(default["revision"], int)
            or isinstance(default["revision"], bool)
            or not isinstance(value.get("paused"), bool)
            or not isinstance(kill_switch, dict)
            or not isinstance(kill_switch.get("enabled"), bool)
        ):
            raise ValueError("invalid-control")
        return default

    def _save(self, control: dict[str, Any]) -> None:
        expected_revision = control.get("revision")
        if not isinstance(expected_revision, int) or isinstance(
            expected_revision, bool
        ):
            raise RuntimeError("invalid-control-revision")
        if self.control_path.exists():
            current = _read_json(self.control_path, None)
            if not isinstance(current, dict):
                raise RuntimeError("invalid-control")
            current_revision = current.get("revision")
        else:
            current_revision = 0
        if current_revision != expected_revision:
            raise RuntimeError("control-revision-conflict")
        updated = dict(control)
        updated["schemaVersion"] = "1.0"
        updated["revision"] = expected_revision + 1
        updated["updatedAt"] = _now()
        _atomic_json(self.control_path, updated)

    def _state_name(self, control: dict[str, Any]) -> str:
        if control["killSwitch"]["enabled"]:
            return "kill-switch"
        if control["paused"]:
            return "paused"
        return "active"

    def status(self) -> dict[str, Any]:
        try:
            control = self._load()
        except ValueError:
            return {
                "schemaVersion": "1.0",
                "status": "invalid-control",
                "queues": self._queue_summary(),
            }
        return {
            "schemaVersion": "1.0",
            "status": self._state_name(control),
            "control": control,
            "queues": self._queue_summary(),
        }

    def pause(self, reason: str) -> dict[str, Any]:
        with _ControlMutationLock(self.state_dir / "control.lock"):
            control = self._load()
            control["paused"] = True
            control["pauseReason"] = reason[:500] if reason else "manual"
            self._save(control)
        return self.status()

    def resume(self) -> dict[str, Any]:
        with _ControlMutationLock(self.state_dir / "control.lock"):
            control = self._load()
            if control["killSwitch"]["enabled"]:
                return {
                    "schemaVersion": "1.0",
                    "status": "blocked",
                    "reason": "kill-switch-enabled",
                    "control": control,
                }
            control["paused"] = False
            control["pauseReason"] = None
            self._save(control)
        return self.status()

    def set_kill_switch(self, enabled: bool, reason: str) -> dict[str, Any]:
        with _ControlMutationLock(self.state_dir / "control.lock"):
            control = self._load()
            control["killSwitch"] = {
                "enabled": bool(enabled),
                "reason": (reason[:500] if reason else "manual"),
                "changedAt": _now(),
            }
            if enabled:
                control["paused"] = True
                control["pauseReason"] = "kill-switch"
            self._save(control)
        return self.status()

    def health(self) -> dict[str, Any]:
        try:
            control = self._load()
        except ValueError:
            return {
                "schemaVersion": "1.0",
                "status": "invalid-control",
                "healthy": False,
                "queues": self._queue_summary(),
                "lease": self._lease_health(),
                "routerStatePresent": (self.state_dir / "router-state.json").is_file(),
                "controlFilePresent": True,
            }
        lease = self._lease_health()
        state = self._state_name(control)
        return {
            "schemaVersion": "1.0",
            "status": state,
            "healthy": state != "kill-switch" and lease["status"] != "invalid",
            "queues": self._queue_summary(),
            "lease": lease,
            "routerStatePresent": (self.state_dir / "router-state.json").is_file(),
            "controlFilePresent": self.control_path.is_file(),
        }

    def _queue_summary(self) -> dict[str, int]:
        return {
            "spool": _queue_count(self.state_dir / "spool"),
            "deadLetter": _queue_count(self.state_dir / "dead-letter"),
            "receipts": _queue_count(self.state_dir / "receipts"),
        }

    def _lease_health(self) -> dict[str, Any]:
        lock_path = self.state_dir / "router.lock"
        if not lock_path.exists():
            return {"status": "absent"}
        value = _read_json(lock_path, None)
        if not isinstance(value, dict):
            return {"status": "invalid"}
        expiry = value.get("expiresAt")
        if not isinstance(expiry, str):
            return {"status": "invalid"}
        normalized = expiry[:-1] + "+00:00" if expiry.endswith("Z") else expiry
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return {"status": "invalid"}
        if parsed.tzinfo is None:
            return {"status": "invalid"}
        return {
            "status": "live" if parsed.astimezone(UTC) > datetime.now(UTC) else "stale",
            "expiresAt": expiry,
        }

    def export_plan(
        self,
        *,
        registry_path: Path,
        skills_root: Path,
        plugin_name: str,
        selected_keys: list[str] | None,
    ) -> dict[str, Any]:
        module_path = (
            Path(__file__).resolve().parents[1] / "packaging" / "export_plan.py"
        )
        spec = importlib.util.spec_from_file_location(
            "adaptive_export_plan", module_path
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("export plan generator is unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.build_export_plan(
            registry_path=registry_path,
            skills_root=skills_root,
            plugin_name=plugin_name,
            selected_keys=selected_keys,
        )


def _default_state() -> Path:
    configured = os.environ.get("ADAPTIVE_STATE_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[1] / "state"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Local JSON control surface for adaptive runtime."
    )
    parser.add_argument("--state", type=Path, default=_default_state())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("health")
    commands.add_parser("status")
    pause = commands.add_parser("pause")
    pause.add_argument("--reason", default="manual")
    commands.add_parser("resume")
    kill = commands.add_parser("kill-switch")
    kill.add_argument("mode", choices=("on", "off"))
    kill.add_argument("--reason", default="manual")
    export = commands.add_parser("export-plan")
    export.add_argument("--registry", type=Path, required=True)
    export.add_argument("--skills-root", type=Path, required=True)
    export.add_argument("--plugin-name", required=True)
    export.add_argument("--skill", action="append", dest="skills")
    args = parser.parse_args()

    surface = ControlSurface(args.state)
    try:
        if args.command == "health":
            result = surface.health()
        elif args.command == "status":
            result = surface.status()
        elif args.command == "pause":
            result = surface.pause(args.reason)
        elif args.command == "resume":
            result = surface.resume()
        elif args.command == "kill-switch":
            result = surface.set_kill_switch(args.mode == "on", args.reason)
        else:
            result = surface.export_plan(
                registry_path=args.registry,
                skills_root=args.skills_root,
                plugin_name=args.plugin_name,
                selected_keys=args.skills,
            )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
