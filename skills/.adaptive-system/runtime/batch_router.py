from __future__ import annotations

import argparse
import json
import math
import os
import re
import signal
import string
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from process_lock import LocalProcessLock


UTC = timezone.utc
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+$")
CONTENT_REF_RE = re.compile(
    r"^(?:vault|content|blob)://(?:sha256/[0-9a-f]{64}|opaque/[A-Za-z0-9_-]{16,128})$"
)


def _now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _strict_event(event: Any, event_id: str) -> dict[str, Any] | None:
    if not isinstance(event, dict) or set(event) != {
        "schemaVersion",
        "eventId",
        "occurredAt",
        "eventType",
        "source",
        "correlation",
        "contentRefs",
        "metadata",
        "privacy",
    }:
        return None
    if (
        event.get("schemaVersion") != "1.0"
        or event.get("eventId") != event_id
        or not ID_RE.fullmatch(event_id)
        or not isinstance(event.get("eventType"), str)
        or not EVENT_TYPE_RE.fullmatch(event["eventType"])
        or _parse_timestamp(event.get("occurredAt")) is None
    ):
        return None
    source = event.get("source")
    if (
        not isinstance(source, dict)
        or not {"kind", "name"} <= set(source) <= {"kind", "name", "version"}
        or source.get("kind") not in {"hook", "scheduler", "manual", "adapter"}
        or not isinstance(source.get("name"), str)
        or not ID_RE.fullmatch(source["name"])
        or ("version" in source and not isinstance(source["version"], str))
    ):
        return None
    correlation = event.get("correlation")
    if not isinstance(correlation, dict) or not set(correlation) <= {
        "projectId",
        "sessionId",
        "turnId",
        "toolUseId",
    }:
        return None
    if any(
        not isinstance(value, str) or not ID_RE.fullmatch(value)
        for value in correlation.values()
    ):
        return None
    refs = event.get("contentRefs")
    if (
        not isinstance(refs, list)
        or len(refs) > 32
        or len(set(refs)) != len(refs)
        or any(
            not isinstance(ref, str) or not CONTENT_REF_RE.fullmatch(ref)
            for ref in refs
        )
    ):
        return None
    metadata = event.get("metadata")
    if not isinstance(metadata, dict) or not set(metadata) <= {
        "toolName",
        "status",
        "exitCode",
        "durationMs",
        "isError",
        "contentBytesDiscarded",
    }:
        return None
    for key in ("toolName", "status"):
        if key in metadata and (
            not isinstance(metadata[key], str) or not ID_RE.fullmatch(metadata[key])
        ):
            return None
    for key in ("exitCode", "durationMs", "contentBytesDiscarded"):
        if key in metadata and (
            not isinstance(metadata[key], int) or isinstance(metadata[key], bool)
        ):
            return None
    if any(metadata.get(key, 0) < 0 for key in ("durationMs", "contentBytesDiscarded")):
        return None
    if "isError" in metadata and not isinstance(metadata["isError"], bool):
        return None
    privacy = event.get("privacy")
    if (
        not isinstance(privacy, dict)
        or not {"rawContentStored"}
        <= set(privacy)
        <= {
            "rawContentStored",
            "redactionVersion",
        }
        or privacy.get("rawContentStored") is not False
        or (
            "redactionVersion" in privacy
            and not isinstance(privacy["redactionVersion"], str)
        )
    ):
        return None
    return {
        "schemaVersion": "1.0",
        "eventId": event_id,
        "occurredAt": event["occurredAt"],
        "eventType": event["eventType"],
        "source": dict(source),
        "correlation": dict(correlation),
        "contentRefs": list(refs),
        "metadata": dict(metadata),
        "privacy": dict(privacy),
    }


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default


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


def _assign_windows_kill_job(process: subprocess.Popen[str]) -> int | None:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        information = ExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = 0x00002000
        if not kernel32.SetInformationJobObject(
            job,
            9,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            kernel32.CloseHandle(job)
            return None
        if not kernel32.AssignProcessToJobObject(
            job,
            wintypes.HANDLE(int(process._handle)),  # type: ignore[attr-defined]
        ):
            kernel32.CloseHandle(job)
            return None
        return int(job)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _stop_process_tree(process: subprocess.Popen[str], windows_job: int | None) -> None:
    if os.name == "nt" and windows_job:
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CloseHandle(wintypes.HANDLE(windows_job))
        except (AttributeError, OSError):
            pass
    elif os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        pass


@dataclass
class RouterResult:
    status: str
    processed: int = 0
    failed: int = 0
    deduplicated: int = 0
    deferred: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class RouterLease:
    def __init__(
        self,
        path: Path,
        now_fn: Callable[[], datetime],
        lease_seconds: int,
    ) -> None:
        self.path = path
        self.now_fn = now_fn
        self.lease_seconds = max(1, lease_seconds)
        self.owner = f"router-{uuid.uuid4().hex}"
        self.acquired = False
        self.process_lock = LocalProcessLock(
            self.path.with_name(f"{self.path.name}.guard"),
        )

    def acquire(self) -> bool:
        if self.acquired:
            return True
        if not self.process_lock.acquire():
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            for _ in range(2):
                now = self.now_fn()
                payload = {
                    "owner": self.owner,
                    "acquiredAt": _timestamp(now),
                    "expiresAt": _timestamp(
                        now + timedelta(seconds=self.lease_seconds)
                    ),
                }
                try:
                    descriptor = os.open(
                        self.path,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    )
                except FileExistsError:
                    current = _load_json(self.path, {})
                    expiry = _parse_timestamp(current.get("expiresAt"))
                    if expiry is not None and expiry > now:
                        return False
                    if expiry is None:
                        try:
                            age = now - datetime.fromtimestamp(
                                self.path.stat().st_mtime,
                                tz=UTC,
                            )
                        except OSError:
                            return False
                        if age < timedelta(seconds=self.lease_seconds):
                            return False
                    try:
                        self.path.unlink()
                    except OSError:
                        return False
                    continue
                try:
                    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                        json.dump(
                            payload, handle, sort_keys=True, separators=(",", ":")
                        )
                        handle.flush()
                        os.fsync(handle.fileno())
                except Exception:
                    self.path.unlink(missing_ok=True)
                    raise
                self.acquired = True
                return True
            return False
        finally:
            if not self.acquired:
                self.process_lock.release()

    def release(self) -> None:
        if not self.acquired:
            self.process_lock.release()
            return
        try:
            current = _load_json(self.path, {})
            if current.get("owner") == self.owner:
                self.path.unlink(missing_ok=True)
        finally:
            self.acquired = False
            self.process_lock.release()

    def refresh(self) -> bool:
        if not self.acquired:
            return False
        current = _load_json(self.path, None)
        if not isinstance(current, dict) or current.get("owner") != self.owner:
            self.acquired = False
            self.process_lock.release()
            return False
        now = self.now_fn()
        _atomic_json(
            self.path,
            {
                "owner": self.owner,
                "acquiredAt": current.get("acquiredAt", _timestamp(now)),
                "expiresAt": _timestamp(now + timedelta(seconds=self.lease_seconds)),
            },
        )
        return True

    def __enter__(self) -> "RouterLease":
        if not self.acquire():
            raise RuntimeError("router-lease-unavailable")
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


class BatchRouter:
    def __init__(
        self,
        state_dir: Path | str,
        config: Mapping[str, Any],
        *,
        execute_adapters: bool = False,
        now_fn: Callable[[], datetime] = _now,
    ) -> None:
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.spool_dir = self.state_dir / "spool"
        self.receipts_dir = self.state_dir / "receipts"
        self.attempts_dir = self.state_dir / "attempts"
        self.dead_letter_dir = self.state_dir / "dead-letter"
        self.state_path = self.state_dir / "router-state.json"
        self.lock_path = self.state_dir / "router.lock"
        self.config = dict(config)
        self.execute_adapters = execute_adapters
        self.now_fn = now_fn

    def run(self) -> RouterResult:
        lease_seconds = self._effective_lease_seconds()
        lease = RouterLease(self.lock_path, self.now_fn, lease_seconds)
        if not lease.acquire():
            return RouterResult("locked")
        try:
            return self._run_acquired(lease)
        except Exception as exc:
            return RouterResult("router-error", errors=[type(exc).__name__])
        finally:
            lease.release()

    def _effective_lease_seconds(self) -> int:
        configured = _positive_int(self.config.get("leaseSeconds"), 30)
        budget = self.config.get("budget")
        budget_seconds = _safe_positive_float(
            budget.get("maxSeconds") if isinstance(budget, dict) else None,
            10,
        )
        adapters = self.config.get("adapters")
        adapter_values = adapters.values() if isinstance(adapters, dict) else ()
        timeout_values = [
            _safe_positive_float(adapter.get("timeoutSeconds"), 10)
            for adapter in adapter_values
            if isinstance(adapter, dict)
        ]
        longest_adapter = max(timeout_values, default=10)
        return max(configured, math.ceil(budget_seconds + longest_adapter + 5))

    def _control_gate_status(self) -> str | None:
        path = self.state_dir / "control.json"
        if not path.exists():
            return None
        control = _load_json(path, None)
        if (
            not isinstance(control, dict)
            or control.get("schemaVersion") != "1.0"
            or not isinstance(control.get("revision"), int)
            or isinstance(control.get("revision"), bool)
            or not isinstance(control.get("paused"), bool)
            or not isinstance(control.get("killSwitch"), dict)
            or not isinstance(control["killSwitch"].get("enabled"), bool)
        ):
            return "invalid-control"
        if control["killSwitch"]["enabled"]:
            return "kill-switch"
        if control["paused"]:
            return "paused"
        return None

    def _run_acquired(self, lease: RouterLease) -> RouterResult:
        result = RouterResult("completed")
        control_status = self._control_gate_status()
        if control_status:
            return RouterResult(control_status)
        self.spool_dir.mkdir(parents=True, exist_ok=True)
        self.receipts_dir.mkdir(parents=True, exist_ok=True)
        state = _load_json(self.state_path, {"circuits": {}})
        if not isinstance(state, dict):
            state = {"circuits": {}}
        if not isinstance(state.get("circuits"), dict):
            state["circuits"] = {}

        budget = self.config.get("budget")
        if not isinstance(budget, dict):
            budget = {}
        max_items = _positive_int(budget.get("maxItems"), 100)
        max_adapter_runs = _nonnegative_int(budget.get("maxAdapterRuns"), 25)
        max_seconds = _safe_positive_float(budget.get("maxSeconds"), 10)
        adapter_runs = 0
        started = time.monotonic()
        seen = 0

        for event_path in sorted(self.spool_dir.glob("*.json")):
            control_status = self._control_gate_status()
            if control_status:
                result.status = control_status
                break
            if not lease.refresh():
                result.status = "lease-lost"
                break
            if seen >= max_items:
                result.status = "budget-exhausted"
                break
            seen += 1
            if time.monotonic() - started >= max_seconds:
                result.status = "budget-exhausted"
                break
            event_id = event_path.stem
            receipt_path = self.receipts_dir / f"{event_id}.json"
            if receipt_path.exists():
                event_path.unlink(missing_ok=True)
                result.deduplicated += 1
                continue

            event = _strict_event(_load_json(event_path, None), event_id)
            if event is None:
                self._record_failure(event_path, event_id, "invalid-event", None, state)
                result.failed += 1
                continue

            routes_config = self.config.get("routes")
            raw_route = (
                routes_config.get(event["eventType"])
                if isinstance(routes_config, dict)
                else None
            )
            if isinstance(raw_route, str) and raw_route:
                routes = [raw_route]
            elif (
                isinstance(raw_route, list)
                and raw_route
                and all(isinstance(item, str) and item for item in raw_route)
            ):
                routes = list(dict.fromkeys(raw_route))
            else:
                routes = []
            if not routes:
                _atomic_json(
                    receipt_path,
                    {
                        "eventId": event_id,
                        "status": "unrouted",
                        "processedAt": _timestamp(self.now_fn()),
                    },
                )
                event_path.unlink(missing_ok=True)
                result.processed += 1
                continue

            if not self.execute_adapters:
                result.deferred += 1
                continue

            event_complete = True
            event_failed = False
            adapter_receipts_dir = self.state_dir / "adapter-receipts" / event_id
            adapters_config = self.config.get("adapters")
            if not isinstance(adapters_config, dict):
                adapters_config = {}
            for route in routes:
                control_status = self._control_gate_status()
                if control_status:
                    result.status = control_status
                    event_complete = False
                    break
                if time.monotonic() - started >= max_seconds:
                    result.status = "budget-exhausted"
                    event_complete = False
                    break
                if not lease.refresh():
                    result.status = "lease-lost"
                    event_complete = False
                    break
                adapter_token = _path_token(route)
                adapter_receipt = adapter_receipts_dir / f"{adapter_token}.json"
                if adapter_receipt.exists():
                    continue
                circuit = state["circuits"].get(route, {})
                if self._circuit_is_open(circuit):
                    result.deferred += 1
                    event_complete = False
                    continue
                if adapter_runs >= max_adapter_runs:
                    result.status = "budget-exhausted"
                    event_complete = False
                    break
                adapter = adapters_config.get(route)
                if not isinstance(adapter, dict):
                    self._record_failure(
                        event_path,
                        event_id,
                        "unknown-adapter",
                        route,
                        state,
                    )
                    event_failed = True
                    event_complete = False
                    break
                if adapter.get("enabled") is not True:
                    result.deferred += 1
                    event_complete = False
                    continue
                if (
                    adapter.get("inputContract", "EventEnvelope/1.0")
                    != "EventEnvelope/1.0"
                ):
                    self._record_failure(
                        event_path,
                        event_id,
                        "unsupported-input-contract",
                        route,
                        state,
                    )
                    event_failed = True
                    event_complete = False
                    break
                if (
                    adapter.get("requiresContentRef") is True
                    and not event["contentRefs"]
                ):
                    result.deferred += 1
                    event_complete = False
                    continue
                adapter_runs += 1
                outcome = self._execute_adapter(
                    adapter,
                    event,
                    stop_check=self._control_gate_status,
                )
                if outcome["success"]:
                    _atomic_json(
                        adapter_receipt,
                        {
                            "eventId": event_id,
                            "status": "processed",
                            "adapter": route,
                            "exitCode": outcome["exitCode"],
                            "durationMs": outcome["durationMs"],
                            "processedAt": _timestamp(self.now_fn()),
                        },
                    )
                    (self.attempts_dir / f"{event_id}--{adapter_token}.json").unlink(
                        missing_ok=True
                    )
                    state["circuits"][route] = {
                        "state": "closed",
                        "consecutiveFailures": 0,
                    }
                elif outcome.get("stopped"):
                    result.status = outcome.get("controlStatus", "paused")
                    event_complete = False
                    break
                else:
                    self._record_failure(
                        event_path,
                        event_id,
                        outcome["reason"],
                        route,
                        state,
                        exit_code=outcome["exitCode"],
                        duration_ms=outcome["durationMs"],
                    )
                    event_failed = True
                    event_complete = False
                    break

            if event_failed:
                result.failed += 1
            elif event_complete and event_path.exists():
                _atomic_json(
                    receipt_path,
                    {
                        "eventId": event_id,
                        "status": "processed",
                        "adapters": routes,
                        "processedAt": _timestamp(self.now_fn()),
                    },
                )
                event_path.unlink(missing_ok=True)
                result.processed += 1
            if result.status in {
                "budget-exhausted",
                "lease-lost",
                "kill-switch",
                "paused",
                "invalid-control",
            }:
                break

        _atomic_json(self.state_path, state)
        return result

    def _execute_adapter(
        self,
        adapter: Mapping[str, Any],
        event: Mapping[str, Any],
        *,
        stop_check: Callable[[], str | None],
    ) -> dict[str, Any]:
        command = adapter.get("command")
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(part, str) and part for part in command)
        ):
            return {
                "success": False,
                "reason": "invalid-command",
                "exitCode": None,
                "durationMs": 0,
            }
        variables = {
            "PYTHON": sys.executable,
            "ADAPTIVE_SYSTEM_ROOT": str(Path(__file__).resolve().parents[1]),
            "SKILLS_ROOT": str(Path(__file__).resolve().parents[2]),
        }
        for key, value in os.environ.items():
            if re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
                variables.setdefault(key, value)
        command = [string.Template(part).safe_substitute(variables) for part in command]
        timeout = min(_safe_positive_float(adapter.get("timeoutSeconds"), 10), 300)
        started = time.monotonic()
        process: subprocess.Popen[str] | None = None
        windows_job: int | None = None
        try:
            process = subprocess.Popen(
                command,
                text=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                start_new_session=os.name != "nt",
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
                ),
            )
            windows_job = _assign_windows_kill_job(process)
            payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            if process.stdin is None:
                raise OSError("adapter stdin unavailable")
            process.stdin.write(payload)
            process.stdin.close()
            process.stdin = None
            while True:
                return_code = process.poll()
                if return_code is not None:
                    _stop_process_tree(process, windows_job)
                    process = None
                    return {
                        "success": return_code == 0,
                        "reason": "adapter-exit" if return_code else "ok",
                        "exitCode": return_code,
                        "durationMs": int((time.monotonic() - started) * 1000),
                    }
                control_status = stop_check()
                if control_status:
                    _stop_process_tree(process, windows_job)
                    process = None
                    return {
                        "success": False,
                        "stopped": True,
                        "controlStatus": control_status,
                        "reason": "control-stop",
                        "exitCode": None,
                        "durationMs": int((time.monotonic() - started) * 1000),
                    }
                if time.monotonic() - started >= timeout:
                    _stop_process_tree(process, windows_job)
                    process = None
                    return {
                        "success": False,
                        "reason": "adapter-timeout",
                        "exitCode": None,
                        "durationMs": int((time.monotonic() - started) * 1000),
                    }
                time.sleep(0.05)
        except OSError:
            return {
                "success": False,
                "reason": "adapter-start-error",
                "exitCode": None,
                "durationMs": int((time.monotonic() - started) * 1000),
            }
        finally:
            if process is not None:
                _stop_process_tree(process, windows_job)

    def _record_failure(
        self,
        event_path: Path,
        event_id: str,
        reason: str,
        adapter: str | None,
        state: dict[str, Any],
        *,
        exit_code: int | None = None,
        duration_ms: int | None = None,
    ) -> None:
        adapter_token = _path_token(adapter or "unrouted")
        attempt_path = self.attempts_dir / f"{event_id}--{adapter_token}.json"
        attempts = _load_json(attempt_path, {})
        count = int(attempts.get("count", 0)) + 1 if isinstance(attempts, dict) else 1
        record = {
            "eventId": event_id,
            "count": count,
            "reason": reason,
            "adapter": adapter,
            "exitCode": exit_code,
            "durationMs": duration_ms,
            "updatedAt": _timestamp(self.now_fn()),
        }
        _atomic_json(attempt_path, record)
        max_retries = _positive_int(self.config.get("maxRetries"), 3)
        if count >= max_retries:
            self.dead_letter_dir.mkdir(parents=True, exist_ok=True)
            os.replace(event_path, self.dead_letter_dir / event_path.name)

        if adapter:
            breaker_config = self.config.get("circuitBreaker")
            if not isinstance(breaker_config, dict):
                breaker_config = {}
            threshold = _positive_int(breaker_config.get("failureThreshold"), 3)
            cooldown = _positive_int(breaker_config.get("cooldownSeconds"), 60)
            prior = state["circuits"].get(adapter, {})
            failures = int(prior.get("consecutiveFailures", 0)) + 1
            circuit = {"state": "closed", "consecutiveFailures": failures}
            if failures >= threshold:
                circuit.update(
                    {
                        "state": "open",
                        "openedAt": _timestamp(self.now_fn()),
                        "retryAfter": _timestamp(
                            self.now_fn() + timedelta(seconds=cooldown)
                        ),
                    }
                )
            state["circuits"][adapter] = circuit

    def _circuit_is_open(self, circuit: Any) -> bool:
        if not isinstance(circuit, dict) or circuit.get("state") != "open":
            return False
        retry_after = _parse_timestamp(circuit.get("retryAfter"))
        return retry_after is None or retry_after > self.now_fn()


def _positive_int(value: Any, default: int) -> int:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value > 0
        else default
    )


def _nonnegative_int(value: Any, default: int) -> int:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else default
    )


def _safe_positive_float(value: Any, default: float) -> float:
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(converted) or converted <= 0:
        return default
    return converted


def _path_token(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", value):
        return value
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")[:48] or "adapter"
    return f"{safe}-{uuid.uuid5(uuid.NAMESPACE_URL, value).hex[:16]}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bounded single-lease batch event router."
    )
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--execute-adapters",
        action="store_true",
        help="Explicitly allow configured adapter argv commands. Default is dry routing.",
    )
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text(encoding="utf-8-sig"))
        if not isinstance(config, dict):
            raise ValueError("config root must be an object")
        result = BatchRouter(
            args.state,
            config,
            execute_adapters=args.execute_adapters,
        ).run()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "invalid-config", "reason": type(exc).__name__}))
        return 2
    print(json.dumps(result.to_json(), ensure_ascii=False, sort_keys=True))
    return 0 if result.status not in {"router-error"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
