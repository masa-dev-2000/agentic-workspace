from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


UTC = timezone.utc
MAX_INPUT_BYTES = 8 * 1024 * 1024
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
FILE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
REFERENCE_RE = re.compile(
    r"^(?:vault|content|blob)://(?:sha256/[0-9a-f]{64}|opaque/[A-Za-z0-9_-]{16,128})$"
)
EVENT_TYPES = {
    "PreToolUse": "execution.pre-tool",
    "PostToolUse": "execution.post-tool",
    "PostToolUseFailure": "execution.tool-failure",
    "UserPromptSubmit": "interaction.user-prompt",
    "Stop": "interaction.stop",
    "SessionStart": "lifecycle.session-start",
    "SessionEnd": "lifecycle.session-end",
}


@dataclass(frozen=True)
class DispatchResult:
    status: str
    stored: bool
    duplicate: bool = False
    path: Path | None = None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _valid_id(value: Any) -> str | None:
    if isinstance(value, str) and ID_RE.fullmatch(value):
        return value
    return None


def _safe_timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _pick(source: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in source:
            return source[name]
    return None


def _content_refs(source: Mapping[str, Any]) -> list[str]:
    values: list[Any] = []
    for key in ("contentRef", "content_ref"):
        if key in source:
            values.append(source[key])
    for key in ("contentRefs", "content_refs"):
        value = source.get(key)
        if isinstance(value, list):
            values.extend(value)
    refs: list[str] = []
    for value in values:
        if (
            isinstance(value, str)
            and len(value) <= 2048
            and REFERENCE_RE.match(value)
            and value not in refs
        ):
            refs.append(value)
    return refs[:32]


def _safe_int(value: Any, minimum: int | None = None) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and (minimum is None or value >= minimum):
        return value
    return None


def _event_envelope(
    source: Mapping[str, Any], raw_size: int, event_override: str | None
) -> dict[str, Any]:
    hook_name = event_override or _pick(
        source, "hook_event_name", "hookEventName", "event"
    )
    hook_name = _valid_id(hook_name) or "UnknownHook"
    slug = re.sub(r"[^a-z0-9-]+", "-", hook_name.lower()).strip("-") or "unknown"
    event_type = EVENT_TYPES.get(hook_name, f"hook.{slug}")

    correlation: dict[str, str] = {}
    for output, names in {
        "projectId": ("project_id", "projectId"),
        "sessionId": ("session_id", "sessionId"),
        "turnId": ("turn_id", "turnId"),
        "toolUseId": ("tool_use_id", "toolUseId"),
    }.items():
        value = _valid_id(_pick(source, *names))
        if value:
            correlation[output] = value

    metadata: dict[str, Any] = {}
    tool_name = _valid_id(_pick(source, "tool_name", "toolName"))
    status = _valid_id(_pick(source, "status", "outcome"))
    exit_code = _safe_int(_pick(source, "exit_code", "exitCode"))
    duration_ms = _safe_int(_pick(source, "duration_ms", "durationMs"), minimum=0)
    is_error = _pick(source, "is_error", "isError")
    if tool_name:
        metadata["toolName"] = tool_name
    if status:
        metadata["status"] = status
    if exit_code is not None:
        metadata["exitCode"] = exit_code
    if duration_ms is not None:
        metadata["durationMs"] = duration_ms
    if isinstance(is_error, bool):
        metadata["isError"] = is_error
    metadata["contentBytesDiscarded"] = raw_size

    refs = _content_refs(source)
    proposed_event_id = _pick(source, "event_id", "eventId")
    if isinstance(proposed_event_id, str) and FILE_ID_RE.fullmatch(proposed_event_id):
        event_id = proposed_event_id
    else:
        event_id = f"evt-{uuid.uuid4().hex}"
    occurred_at = (
        _safe_timestamp(_pick(source, "occurred_at", "occurredAt", "timestamp"))
        or _utc_now()
    )

    return {
        "schemaVersion": "1.0",
        "eventId": event_id,
        "occurredAt": occurred_at,
        "eventType": event_type,
        "source": {"kind": "hook", "name": hook_name, "version": "1.0"},
        "correlation": correlation,
        "contentRefs": refs,
        "metadata": metadata,
        "privacy": {"rawContentStored": False, "redactionVersion": "1.0"},
    }


def _atomic_create(path: Path, content: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return False
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp_path, path)
        except FileExistsError:
            return False
        except OSError:
            claim = path.with_name(f".{path.name}.claim")
            try:
                claim_descriptor = os.open(
                    claim,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                )
            except FileExistsError:
                return False
            try:
                os.close(claim_descriptor)
                if path.exists():
                    return False
                os.replace(temp_path, path)
                temp_path = None
            finally:
                claim.unlink(missing_ok=True)
        return True
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def dispatch_bytes(
    raw: bytes,
    spool_dir: Path | str,
    *,
    event_override: str | None = None,
) -> DispatchResult:
    if len(raw) > MAX_INPUT_BYTES:
        return DispatchResult("input-too-large", False)
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return DispatchResult("invalid-input", False)
    if not isinstance(value, dict):
        return DispatchResult("invalid-input", False)
    try:
        envelope = _event_envelope(value, len(raw), event_override)
        payload = json.dumps(
            envelope,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        destination = Path(spool_dir) / f"{envelope['eventId']}.json"
        stored = _atomic_create(destination, payload)
        if not stored:
            return DispatchResult("duplicate", False, duplicate=True, path=destination)
        return DispatchResult("stored", True, path=destination)
    except Exception:
        # Lifecycle hooks must never block or fail the original operation.
        return DispatchResult("storage-error", False)


def _default_spool() -> Path:
    configured = os.environ.get("ADAPTIVE_SPOOL_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[1] / "state" / "spool"


def main() -> int:
    try:
        parser = argparse.ArgumentParser(
            description="Fail-open, raw-content-free lifecycle hook spooler."
        )
        parser.add_argument("--spool", type=Path, default=_default_spool())
        parser.add_argument("--event")
        args = parser.parse_args()
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        result = dispatch_bytes(raw, args.spool, event_override=args.event)
        print(
            json.dumps(
                {
                    "status": result.status,
                    "stored": result.stored,
                    "duplicate": result.duplicate,
                },
                sort_keys=True,
            )
        )
    except BaseException:
        # Even argument-independent runtime problems remain fail-open.
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
