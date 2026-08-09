from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SPOOL_VERSION = 1
MAX_SPOOL_BYTES = 16_384
HASH_RE = re.compile(r"[0-9a-f]{64}")
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")

FEEDBACK_TYPES = {"complaint", "request", "correction", "preference"}
IMPACTS = {"low", "medium", "high"}
EXPLICITNESS = {"explicit", "inferred"}
SOURCE_KINDS = {"user"}
SUBJECT_KINDS = {"user", "skill", "workflow", "project", "product", "organization", "unknown"}
VALENCES = {"positive", "negative", "mixed", "neutral", "unknown"}
EVIDENCE_ROLES = {"support", "counter", "boundary"}

SPOOL_FIELDS = {
    "version",
    "event_type",
    "event_id",
    "observed_at",
    "session_hash",
    "turn_hash",
    "repo_hash",
    "feedback_type",
    "subject_class",
    "theme_key",
    "impact",
    "explicitness",
    "source_kind",
    "subject_kind",
    "valence",
    "evidence_role",
    "persistence_requested",
    "reaction_signature",
    "auth_tag",
}


class InvalidSpoolEnvelope(ValueError):
    pass


def state_root_fingerprint(root: Path) -> str:
    normalized = os.path.normcase(str(root.expanduser().resolve()))
    return hashlib.sha256(normalized.encode("utf-8", "surrogatepass")).hexdigest()


def purge_tombstone_path(root: Path) -> Path:
    fingerprint = state_root_fingerprint(root)[:16]
    return root.expanduser().resolve().parent / (
        f".feedback-learning-purged-{fingerprint}.json"
    )


def external_runtime_lock_path(root: Path) -> Path:
    fingerprint = state_root_fingerprint(root)[:16]
    return root.expanduser().resolve().parent / (
        f".feedback-learning-runtime-{fingerprint}.lock"
    )


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise InvalidSpoolEnvelope("invalid-json-value") from exc


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def existing_key(key_path: Path) -> bytes | None:
    """Read a provisioned key without creating a directory, key, or database."""
    try:
        encoded = key_path.read_text(encoding="ascii").strip()
        if not re.fullmatch(r"[0-9a-f]{64}", encoded):
            return None
        return bytes.fromhex(encoded)
    except (OSError, ValueError, UnicodeError):
        return None


def keyed_hash(key: bytes, domain: str, value: Any) -> str:
    rendered = "" if value is None else str(value)
    if not rendered:
        return ""
    return hmac.new(
        key,
        f"{domain}\0{rendered}".encode("utf-8", "replace"),
        hashlib.sha256,
    ).hexdigest()


def auth_tag(record_without_tag: dict[str, Any], key: bytes) -> str:
    if not isinstance(key, bytes) or len(key) < 32:
        raise InvalidSpoolEnvelope("key-unavailable")
    return hmac.new(
        key,
        canonical_json(record_without_tag).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def sign_record(record: dict[str, Any], key: bytes) -> dict[str, Any]:
    if "auth_tag" in record:
        raise InvalidSpoolEnvelope("auth-tag-already-present")
    signed = dict(record)
    signed["auth_tag"] = auth_tag(record, key)
    validate_record(signed, key)
    return signed


def _require_slug(value: Any, name: str) -> str:
    if not isinstance(value, str) or not SLUG_RE.fullmatch(value):
        raise InvalidSpoolEnvelope(f"invalid-{name}")
    return value


def _require_hash(value: Any, name: str, *, allow_empty: bool = False) -> str:
    if value == "" and allow_empty:
        return ""
    if not isinstance(value, str) or not HASH_RE.fullmatch(value):
        raise InvalidSpoolEnvelope(f"invalid-{name}")
    return value


def _validate_timestamp(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 40:
        raise InvalidSpoolEnvelope("invalid-observed-at")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidSpoolEnvelope("invalid-observed-at") from exc
    if parsed.tzinfo is None:
        raise InvalidSpoolEnvelope("invalid-observed-at")
    canonical = parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    if value != canonical:
        raise InvalidSpoolEnvelope("noncanonical-observed-at")
    return value


def validate_record(record: Any, key: bytes) -> dict[str, Any]:
    """Authenticate and enforce an exact, body-free envelope allowlist."""
    if not isinstance(record, dict):
        raise InvalidSpoolEnvelope("envelope-not-object")
    if set(record) != SPOOL_FIELDS:
        raise InvalidSpoolEnvelope("unknown-or-missing-field")
    if record.get("version") != SPOOL_VERSION:
        raise InvalidSpoolEnvelope("unsupported-version")
    if record.get("event_type") != "feedback":
        raise InvalidSpoolEnvelope("invalid-event-type")
    _require_hash(record.get("event_id"), "event-id")
    _validate_timestamp(record.get("observed_at"))
    for name in ("session_hash", "turn_hash", "repo_hash"):
        _require_hash(record.get(name), name.replace("_", "-"), allow_empty=True)
    _require_hash(record.get("reaction_signature"), "reaction-signature")
    supplied = _require_hash(record.get("auth_tag"), "auth-tag")

    if record.get("feedback_type") not in FEEDBACK_TYPES:
        raise InvalidSpoolEnvelope("invalid-feedback-type")
    if record.get("impact") not in IMPACTS:
        raise InvalidSpoolEnvelope("invalid-impact")
    if record.get("explicitness") not in EXPLICITNESS:
        raise InvalidSpoolEnvelope("invalid-explicitness")
    if record.get("source_kind") not in SOURCE_KINDS:
        raise InvalidSpoolEnvelope("invalid-source-kind")
    if record.get("subject_kind") not in SUBJECT_KINDS:
        raise InvalidSpoolEnvelope("invalid-subject-kind")
    if record.get("valence") not in VALENCES:
        raise InvalidSpoolEnvelope("invalid-valence")
    if record.get("evidence_role") not in EVIDENCE_ROLES:
        raise InvalidSpoolEnvelope("invalid-evidence-role")
    _require_slug(record.get("subject_class"), "subject-class")
    _require_slug(record.get("theme_key"), "theme-key")
    if not isinstance(record.get("persistence_requested"), bool):
        raise InvalidSpoolEnvelope("invalid-persistence-requested")

    unsigned = {name: record[name] for name in SPOOL_FIELDS if name != "auth_tag"}
    expected = auth_tag(unsigned, key)
    if not hmac.compare_digest(supplied, expected):
        raise InvalidSpoolEnvelope("invalid-authentication")
    return dict(record)
