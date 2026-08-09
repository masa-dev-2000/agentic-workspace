from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import uuid
from typing import Any, Iterable

from failure_store import (
    COLLECTOR_VERSION,
    FINGERPRINT_VERSION,
    NORMALIZER_VERSION,
    SANITIZER_VERSION,
    authenticate_spool_envelope,
    data_dir,
    disabled_path,
    identity_key_readonly,
    pseudonym,
    stable_hash,
    utc_now,
)
from privacy_contract import redact_credential_assignments

MAX_STDIN_BYTES = 1_000_000
MAX_TEMPLATE_CHARS = 512

FAIL_STATUSES = {"error", "failed", "failure", "timeout", "timed_out", "cancelled", "canceled", "denied"}
AUTHORIZATION_VALUE_RE = re.compile(
    r"""(?ix)
    (?<![A-Za-z0-9_])
    ["']?authorization["']?
    \s*[:=]\s*
    ["']?
    (?:(?:bearer|basic)\s+)?
    [^,\s;"']+
    ["']?
    """
)
AUTH_SCHEME_VALUE_RE = re.compile(
    r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+"
)
JSON_DOUBLE_QUOTED_SECRET_RE = re.compile(
    r"""(?ix)
    (?P<prefix>
      ["']?(?:authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|
      password|passwd|secret|cookie)["']?\s*[:=]\s*
    )
    "(?:\\.|[^"\\])*"
    """
)
JSON_SINGLE_QUOTED_SECRET_RE = re.compile(
    r"""(?ix)
    (?P<prefix>
      ["']?(?:authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|
      password|passwd|secret|cookie)["']?\s*[:=]\s*
    )
    '(?:\\.|[^'\\])*'
    """
)
SECRET_KEY_RE = re.compile(r"(?i)(authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|cookie)\s*[:=]\s*([^\s,;]+)")
URL_QUERY_RE = re.compile(r"([?&][A-Za-z0-9_.~-]+)=([^&#\s]+)")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
WIN_HOME_RE = re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\\s\"']+")
UNIX_HOME_RE = re.compile(r"/(?:home|Users)/[^/\s\"']+")
WIN_DRIVE_PATH_RE = re.compile(
    r"""(?ix)
    (?<![A-Za-z0-9])
    [A-Z]:[\\/]
    [^\s<>:"'|?*,;)\]}]+
    """
)
WIN_UNC_PATH_RE = re.compile(
    r"""(?ix)
    (?<![A-Za-z0-9])
    \\\\
    [^\\\s<>:"'|?*,;)\]}]+
    \\
    [^\\\s<>:"'|?*,;)\]}]+
    (?:\\[^\\\s<>:"'|?*,;)\]}]+)*
    """
)
POSIX_PATH_RE = re.compile(
    r"""(?x)
    (?<![A-Za-z0-9_.:/-])
    /
    (?:[^/\s<>"']+/)*
    [^/\s<>"',;)\]}]+
    """
)
HIGH_ENTROPY_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_+/=-]{32,}(?![A-Za-z0-9])")
UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b")
HEX_RE = re.compile(r"\b0x[0-9a-fA-F]+\b")
PYTHON_EXCEPTION_RE = re.compile(
    r"(?im)\b([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception))\s*:\s*([^\r\n]+)"
)
MODULE_NOT_FOUND_RE = re.compile(
    r"(?i)\b(?:ModuleNotFoundError|ImportError)\s*:\s*No module named\s+['\"]?([^'\"\s;]+)"
)
NODE_MODULE_NOT_FOUND_RE = re.compile(
    r"(?i)\bCannot find module\s+['\"]([^'\"]+)['\"]"
)
REQUEST_ID_RE = re.compile(
    r"(?i)\b(?:request|trace|correlation|activity|operation)[_-]?\s*id\s*[:=]\s*[^\s,;]+"
)
WINDOWS_APPS_PWSH_RE = re.compile(
    r"(?i)\bWindowsApps[\\/]+pwsh\.exe\b"
)
CREATE_PROCESS_ACCESS_DENIED_RE = re.compile(
    r"(?i)\bCreateProcessAsUserW\s+failed\s*:\s*5\b"
)
WIN_ABSOLUTE_PATH_RE = re.compile(r"(?i)\b[A-Z]:\\[^\s\"']+")
POSIX_ABSOLUTE_PATH_RE = re.compile(r"(?<![A-Za-z0-9_.-])/(?:[^/\s\"']+/)*[^/\s\"']+")


def sanitize_text(value: str) -> tuple[str, bool]:
    text = "".join(ch if ch in "\n\t" or ord(ch) >= 32 else " " for ch in value)
    original = text
    text = redact_credential_assignments(text)
    text = AUTHORIZATION_VALUE_RE.sub("authorization=<REDACTED>", text)
    text = AUTH_SCHEME_VALUE_RE.sub(
        lambda match: f"{match.group(1).title()} <REDACTED>",
        text,
    )
    text = JSON_DOUBLE_QUOTED_SECRET_RE.sub(
        lambda match: f'{match.group("prefix")}"<REDACTED>"',
        text,
    )
    text = JSON_SINGLE_QUOTED_SECRET_RE.sub(
        lambda match: f"{match.group('prefix')}'<REDACTED>'",
        text,
    )
    text = SECRET_KEY_RE.sub(lambda m: f"{m.group(1)}=<REDACTED>", text)
    text = URL_QUERY_RE.sub(lambda m: f"{m.group(1)}=<REDACTED>", text)
    text = EMAIL_RE.sub("<EMAIL>", text)
    text = WIN_UNC_PATH_RE.sub("<PATH>", text)
    text = WIN_DRIVE_PATH_RE.sub("<PATH>", text)
    text = POSIX_PATH_RE.sub("<PATH>", text)
    text = WIN_HOME_RE.sub("<HOME>", text)
    text = UNIX_HOME_RE.sub("<HOME>", text)
    text = HIGH_ENTROPY_RE.sub("<TOKEN>", text)
    text = UUID_RE.sub("<UUID>", text)
    text = HEX_RE.sub("<HEX>", text)
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    truncated = len(text) > MAX_TEMPLATE_CHARS
    return text[:MAX_TEMPLATE_CHARS], truncated or text != original


def iter_strings(value: Any, depth: int = 0) -> Iterable[str]:
    if depth > 5:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        priority = ("error", "stderr", "message", "output", "content", "result", "text")
        for key in priority:
            if key in value:
                yield from iter_strings(value[key], depth + 1)
        for key, child in value.items():
            if key not in priority:
                yield from iter_strings(child, depth + 1)
    elif isinstance(value, list):
        for child in value[:20]:
            yield from iter_strings(child, depth + 1)


def _lowered_dict(value: dict[Any, Any]) -> dict[str, Any]:
    return {str(key).lower(): child for key, child in value.items()}


def _has_control_failure(value: dict[Any, Any]) -> bool:
    lowered = _lowered_dict(value)
    if lowered.get("success") is False or lowered.get("is_error") is True or lowered.get("iserror") is True:
        return True
    for key in ("exit_code", "exitcode", "return_code", "returncode"):
        candidate = lowered.get(key)
        if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate != 0:
            return True
    return str(lowered.get("status", "")).lower() in FAIL_STATUSES


def _is_error_content(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    lowered = _lowered_dict(value)
    return (
        lowered.get("is_error") is True
        or lowered.get("iserror") is True
        or str(lowered.get("type", "")).lower() in {"error", "failure"}
        or str(lowered.get("status", "")).lower() in FAIL_STATUSES
    )


def failure_signal(value: Any, depth: int = 0) -> bool:
    """Accept control-plane failures, not error-like words inside successful content."""
    if depth > 2:
        return False
    if isinstance(value, dict):
        lowered = _lowered_dict(value)
        if _has_control_failure(value):
            return True
        if lowered.get("success") is True:
            return False
        for key in ("error", "failure"):
            if key in lowered and lowered[key]:
                return failure_signal(lowered[key], depth + 1) or True
        if lowered.get("exception"):
            return True
        for key in ("stderr", "message"):
            if key in lowered and failure_signal(lowered[key], depth + 1):
                return True
        content = lowered.get("content")
        if isinstance(content, list):
            return any(_is_error_content(item) for item in content[:30])
        if isinstance(content, dict):
            return _is_error_content(content)
        return False
    if isinstance(value, list):
        return depth == 0 and any(failure_signal(child, depth + 1) for child in value[:30])
    if isinstance(value, str):
        text = value.lower()
        patterns = (
            r"^\s*(?:execution error|tool error|script failed|command failed)\b",
            r"^\s*exit code:\s*[1-9]\d*\b",
            r"^\s*(?:access (?:is )?denied|permission denied)\b",
            r"^\s*(?:timeout|timed? out)\b",
            r"^\s*windows error\s+[1-9]\d*\b",
            r"^\s*traceback \(most recent call last\)",
            r"^\s*(?:fatal|uncaught) error\b",
            r"^\s*[a-z_][a-z0-9_.]*(?:error|exception)\s*:",
        )
        return any(re.search(pattern, text) for pattern in patterns)
    return False


def success_signal(value: Any) -> bool:
    if isinstance(value, str):
        return bool(re.search(r"^\s*exit code:\s*0\b", value.lower()))
    if not isinstance(value, dict):
        return False
    lowered = {str(k).lower(): v for k, v in value.items()}
    if lowered.get("success") is True:
        return True
    for key in ("exit_code", "exitcode", "return_code", "returncode"):
        candidate = lowered.get(key)
        if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate == 0:
            return True
    return str(lowered.get("status", "")).lower() in {"ok", "success", "completed", "complete"}


def _iter_text_leaves(value: Any, depth: int = 0) -> Iterable[str]:
    if depth > 4:
        return
    if isinstance(value, str):
        if value.strip():
            yield value
        return
    if isinstance(value, list):
        for child in value[:20]:
            yield from _iter_text_leaves(child, depth + 1)
        return
    if not isinstance(value, dict):
        return

    lowered = _lowered_dict(value)
    exception_type = next(
        (
            lowered[key]
            for key in ("exception_type", "class", "name", "type")
            if isinstance(lowered.get(key), str)
            and re.search(r"(?i)(?:error|exception)$", lowered[key].strip())
        ),
        None,
    )
    exception_message = next(
        (
            lowered[key]
            for key in ("message", "detail", "reason", "text")
            if isinstance(lowered.get(key), str) and lowered[key].strip()
        ),
        None,
    )
    if exception_type and exception_message:
        yield f"{exception_type}: {exception_message}"

    priority = ("message", "detail", "reason", "text", "stderr", "error", "exception")
    for key in priority:
        if key in lowered:
            yield from _iter_text_leaves(lowered[key], depth + 1)


def iter_failure_strings(value: Any, inherited_failure: bool = False, depth: int = 0) -> Iterable[str]:
    """Yield only text carried by an explicit failure envelope or error content."""
    if depth > 4:
        return
    if isinstance(value, str):
        if inherited_failure or failure_signal(value):
            yield value
        return
    if isinstance(value, list):
        for child in value[:30]:
            child_failure = inherited_failure or _is_error_content(child)
            yield from iter_failure_strings(child, child_failure, depth + 1)
        return
    if not isinstance(value, dict):
        return

    lowered = _lowered_dict(value)
    explicit = inherited_failure or _has_control_failure(value)
    for key in ("stderr", "error", "exception"):
        child = lowered.get(key)
        if child:
            yield from _iter_text_leaves(child, depth + 1)
    message = lowered.get("message")
    if message and explicit:
        yield from _iter_text_leaves(message, depth + 1)

    content = lowered.get("content")
    if isinstance(content, list):
        for child in content[:30]:
            child_failure = explicit or _is_error_content(child)
            if child_failure:
                yield from _iter_text_leaves(child, depth + 1)
    elif content is not None and (explicit or _is_error_content(content)):
        yield from _iter_text_leaves(content, depth + 1)


def best_message(response: Any) -> tuple[str, bool]:
    candidates: list[str] = []
    seen: set[str] = set()
    for item in iter_failure_strings(response):
        item = item.strip()
        if item and item not in seen:
            candidates.append(item)
            seen.add(item)
        if len(candidates) >= 8:
            break
    source = "\n".join(candidates) if candidates else "structured tool failure"
    return sanitize_text(source)


def _normalized_cause(value: str) -> str:
    text, _ = sanitize_text(value)
    text = REQUEST_ID_RE.sub("request_id=<ID>", text)
    text = UUID_RE.sub("<UUID>", text)
    text = WIN_ABSOLUTE_PATH_RE.sub("<PATH>", text)
    text = POSIX_ABSOLUTE_PATH_RE.sub("<PATH>", text)
    text = re.sub(r"(?i)\bline\s+\d+\b", "line <N>", text)
    text = re.sub(
        r"(?i)\b(column|char(?:acter)?|offset)\s*[:=]?\s*\d+\b",
        r"\1 <N>",
        text,
    )
    text = re.sub(r"(?i)\b(?:pid|process)\s*[:=#]?\s*\d+\b", "pid <N>", text)
    text = re.sub(r"\b\d{6,}\b", "<N>", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def error_identity(message: str, response: Any) -> str:
    raw_diagnostics = "\n".join(
        item[:4096]
        for _, item in zip(range(16), iter_failure_strings(response))
    )
    if (
        WINDOWS_APPS_PWSH_RE.search(raw_diagnostics)
        and CREATE_PROCESS_ACCESS_DENIED_RE.search(raw_diagnostics)
    ):
        return "launcher-shim-unavailable"

    match = re.search(r"(?i)windows error\s+(\d+)", message)
    if match:
        return f"win32:error_{match.group(1)}"

    module_match = MODULE_NOT_FOUND_RE.search(message) or NODE_MODULE_NOT_FOUND_RE.search(message)
    if module_match:
        module = _normalized_cause(module_match.group(1))
        return f"module:not_found:{stable_hash(module)[:16]}"

    exception_matches = list(PYTHON_EXCEPTION_RE.finditer(message))
    if exception_matches:
        exception = exception_matches[-1]
        exception_class = exception.group(1).lower()
        detail = _normalized_cause(exception.group(2))
        return f"exception:{exception_class}:{stable_hash(detail)[:16]}"

    match = re.search(r"(?i)exit code:\s*(\d+)", message)
    if match:
        return f"process:exit_{match.group(1)}"

    lowered = message.lower()
    if "timed out" in lowered or "timeout" in lowered:
        return "timeout"
    if "permission denied" in lowered or "access denied" in lowered:
        return "permission:denied"
    if "not found" in lowered or "does not exist" in lowered:
        return "resource:not_found"
    if "invalid argument" in lowered or "invalid parameter" in lowered:
        return "invocation:invalid"
    return f"message:{stable_hash(_normalized_cause(message))[:16]}"


def tool_family(tool_name: str) -> str:
    lowered = tool_name.lower()
    if lowered in {"bash", "shell_command", "exec_command"}:
        return "shell"
    if lowered in {"apply_patch", "edit", "write"}:
        return "file-edit"
    if lowered.startswith("mcp__"):
        return "mcp"
    return "local-tool"


def operation_class(tool_name: str, tool_input: Any) -> str:
    family = tool_family(tool_name)
    if family == "shell" and isinstance(tool_input, dict):
        command = tool_input.get("command")
        if isinstance(command, str):
            token = re.match(r"\s*([A-Za-z0-9_.-]+)", command)
            if token:
                return f"shell:{token.group(1).lower()[:32]}"
        return "shell:unknown"
    if family == "file-edit":
        return "file:modify"
    if family == "mcp":
        parts = [part for part in tool_name.lower().split("__") if part]
        server = re.sub(r"[^a-z0-9_.-]+", "-", parts[1] if len(parts) > 1 else "unknown")[:48]
        method = re.sub(r"[^a-z0-9_.-]+", "-", "__".join(parts[2:]) if len(parts) > 2 else "call")[:72]
        return f"mcp:{server}:{method}"
    tool = re.sub(r"[^a-z0-9_.-]+", "-", tool_name.lower()).strip("-") or "unknown"
    return f"tool:{tool[:96]}"


def _first_identifier(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return None


def _readonly_pseudonym(value: str | None, namespace: str) -> str | None:
    """Use a provisioned identity key without creating files from the Hook."""
    if not value:
        return None
    key = identity_key_readonly()
    if key is None:
        return None
    digest = hmac.new(key, f"{namespace}\0{value}".encode("utf-8", "replace"), hashlib.sha256)
    return digest.hexdigest()[:24]


def _fallback_tool_call_hash(
    payload: dict[str, Any],
    name: str,
    operation: str,
    pseudonymizer=pseudonym,
) -> str:
    """HMAC a bounded logical-call envelope when hook IDs are unavailable."""
    source = {
        "hook_event_name": payload.get("hook_event_name"),
        "name": name,
        "operation": operation,
        "cwd": payload.get("cwd"),
        "permission_mode": payload.get("permission_mode"),
        "event_time": next(
            (
                payload.get(key)
                for key in ("observed_at", "timestamp", "event_time", "created_at")
                if payload.get(key) is not None
            ),
            None,
        ),
        "tool_input": payload.get("tool_input"),
        "tool_response": payload.get("tool_response"),
    }
    canonical = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return pseudonymizer(canonical, "tool-call-fallback") or uuid.uuid4().hex[:24]


def spool_event(event: dict[str, Any], *, key: bytes | None = None) -> bool:
    if disabled_path().exists():
        return False
    key_bytes = identity_key_readonly() if key is None else key
    if key_bytes is None:
        return False
    authenticated = authenticate_spool_envelope(event, key_bytes)
    root = data_dir() / "spool"
    root.mkdir(parents=True, exist_ok=True)
    final = root / f"{event['event_id']}.json"
    temp = root / f".{event['event_id']}.{os.getpid()}.tmp"
    temp.write_text(
        json.dumps(authenticated, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    if disabled_path().exists():
        temp.unlink(missing_ok=True)
        return False
    os.replace(temp, final)
    if disabled_path().exists():
        final.unlink(missing_ok=True)
        return False
    return True


def build_event(payload: dict[str, Any], *, pseudonymizer=pseudonym) -> dict[str, Any] | None:
    if payload.get("hook_event_name") != "PostToolUse":
        return None
    response = payload.get("tool_response")
    if not failure_signal(response):
        return None
    name = str(payload.get("tool_name") or "unknown")[:128]
    operation = operation_class(name, payload.get("tool_input"))
    message, changed_or_truncated = best_message(response)
    identity = error_identity(message, response)
    repo_value = _first_identifier(payload, "cwd", "workspace_root")
    repo_hash = pseudonymizer(repo_value, "repo")
    uncorrelated_nonce = uuid.uuid4().hex if repo_value and not repo_hash else None
    environment = {
        "os_family": "windows" if os.name == "nt" else "posix",
        "shell_family": "powershell" if os.name == "nt" else "unknown",
        "permission_mode": str(payload.get("permission_mode") or "unknown")[:64],
    }
    signature_source = json.dumps(
        {
            "tool_name": name,
            "tool_family": tool_family(name),
            "operation_class": operation,
            "repo_hash": repo_hash,
            "uncorrelated_nonce": uncorrelated_nonce,
            "error_identity": identity,
            **environment,
        },
        sort_keys=True,
    )
    session_hash = pseudonymizer(
        _first_identifier(payload, "session_id", "conversation_id", "transcript_path"),
        "session",
    )
    turn_hash = pseudonymizer(
        _first_identifier(payload, "turn_id", "message_id", "prompt_id"),
        "turn",
    )
    tool_call_hash = pseudonymizer(
        _first_identifier(payload, "tool_use_id", "tool_call_id", "call_id", "request_id"),
        "tool-call",
    )
    effective_call_hash = tool_call_hash or _fallback_tool_call_hash(
        payload,
        name,
        operation,
        pseudonymizer,
    )
    idempotency_source = "|".join(
        [
            session_hash or "session:<missing>",
            turn_hash or "turn:<missing>",
            effective_call_hash,
            stable_hash(signature_source)[:24],
        ]
    )
    return {
        "event_type": "failure",
        "event_id": str(uuid.uuid4()),
        "observed_at": utc_now(),
        "idempotency_key": stable_hash(idempotency_source),
        "signature": stable_hash(signature_source),
        "session_hash": session_hash,
        "turn_hash": turn_hash,
        "tool_call_hash": tool_call_hash,
        "repo_hash": repo_hash,
        "tool_name": name,
        "tool_family": tool_family(name),
        "operation_class": operation,
        "outcome_class": "tool-failure-observed",
        "error_identity": identity,
        "message_template": message,
        "capture_mode": "hook",
        "capture_completeness": 0.7,
        "environment": environment,
        "versions": {
            "schema": 1,
            "collector": COLLECTOR_VERSION,
            "sanitizer": SANITIZER_VERSION,
            "normalizer": NORMALIZER_VERSION,
            "fingerprint": FINGERPRINT_VERSION,
        },
        "safety": {
            "secret_scan": "best-effort-passed",
            "redaction_or_truncation_applied": changed_or_truncated,
            "raw_input_stored": False,
            "raw_output_stored": False,
            "repo_correlation": (
                "keyed-pseudonym"
                if repo_hash
                else "unavailable-no-identity-key"
                if repo_value
                else "not-provided"
            ),
        },
    }


def build_recovery_envelope(
    payload: dict[str, Any],
    *,
    pseudonymizer=pseudonym,
) -> dict[str, Any] | None:
    """Build a privacy-safe success marker for deferred recovery linking."""
    if payload.get("hook_event_name") != "PostToolUse":
        return None
    if not success_signal(payload.get("tool_response")):
        return None

    name = str(payload.get("tool_name") or "unknown")[:128]
    operation = operation_class(name, payload.get("tool_input"))
    session_hash = pseudonymizer(
        _first_identifier(payload, "session_id", "conversation_id", "transcript_path"),
        "session",
    )
    repo_hash = pseudonymizer(_first_identifier(payload, "cwd", "workspace_root"), "repo")
    tool_call_hash = pseudonymizer(
        _first_identifier(payload, "tool_use_id", "tool_call_id", "call_id", "request_id"),
        "tool-call",
    )
    effective_call_hash = tool_call_hash or _fallback_tool_call_hash(
        payload,
        name,
        operation,
        pseudonymizer,
    )
    observed_at = utc_now()
    idempotency_source = "|".join(
        [
            "recovery",
            session_hash or "session:<missing>",
            repo_hash or "repo:<missing>",
            name,
            operation,
            effective_call_hash,
        ]
    )
    return {
        "event_type": "recovery",
        "event_id": str(uuid.uuid4()),
        "observed_at": observed_at,
        "idempotency_key": stable_hash(idempotency_source),
        "session_hash": session_hash,
        "repo_hash": repo_hash,
        "tool_name": name,
        "operation_class": operation,
        "versions": {
            "schema": 1,
            "collector": COLLECTOR_VERSION,
            "sanitizer": SANITIZER_VERSION,
            "normalizer": NORMALIZER_VERSION,
            "fingerprint": FINGERPRINT_VERSION,
        },
        "safety": {
            "secret_scan": "not-applicable-no-content-stored",
            "raw_input_stored": False,
            "raw_output_stored": False,
        },
    }


def main() -> int:
    if os.environ.get("CODEX_FAILURE_LEARNING_ACTIVE") == "1" or disabled_path().exists():
        return 0
    os.environ["CODEX_FAILURE_LEARNING_ACTIVE"] = "1"
    try:
        raw = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
        if len(raw) > MAX_STDIN_BYTES:
            return 0
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            return 0
        key = identity_key_readonly()
        if key is None:
            return 0

        def keyed_pseudonym(value: str | None, namespace: str) -> str | None:
            if not value:
                return None
            digest = hmac.new(
                key,
                f"{namespace}\0{value}".encode("utf-8", "replace"),
                hashlib.sha256,
            )
            return digest.hexdigest()[:24]

        event = build_event(payload, pseudonymizer=keyed_pseudonym)
        envelope = event or build_recovery_envelope(
            payload,
            pseudonymizer=keyed_pseudonym,
        )
        if envelope is not None:
            try:
                spool_event(envelope, key=key)
            except OSError:
                pass
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
