from __future__ import annotations

import hashlib
import hmac
import json
import os
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


UTC = timezone.utc


@dataclass(frozen=True)
class ApprovalVerification:
    valid: bool
    reason: str


def _canonical_candidate(document: Mapping[str, Any]) -> dict[str, Any]:
    candidate = deepcopy(dict(document))
    # A document cannot include its own digest in the digest calculation.
    candidate.pop("proposalHash", None)
    return candidate


def approval_hash(document: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _canonical_candidate(document),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _approval_signature_payload(approval: Mapping[str, Any]) -> bytes:
    value = deepcopy(dict(approval))
    value.pop("integrity", None)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sign_approval(
    approval: Mapping[str, Any],
    *,
    key_id: str,
    secret: bytes,
) -> dict[str, Any]:
    if not key_id or not secret:
        raise ValueError("key_id and secret are required")
    signed = deepcopy(dict(approval))
    signature = hmac.new(
        secret,
        _approval_signature_payload(signed),
        hashlib.sha256,
    ).hexdigest()
    signed["integrity"] = {
        "algorithm": "hmac-sha256",
        "keyId": key_id,
        "signature": f"hmac-sha256:{signature}",
    }
    return signed


def _parse_timestamp(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(UTC)


def verify_approval(
    approval: Mapping[str, Any],
    proposal: Mapping[str, Any],
    required_scope: str,
    *,
    now: datetime | None = None,
    trusted_keys: Mapping[str, bytes] | None = None,
) -> ApprovalVerification:
    required_strings = (
        "approvalId",
        "proposalId",
        "approvedHash",
        "decidedBy",
        "decidedAt",
    )
    if approval.get("schemaVersion") != "1.0" or any(
        not isinstance(approval.get(field), str) or not approval.get(field)
        for field in required_strings
    ):
        return ApprovalVerification(False, "invalid-approval")
    if approval.get("decision") != "approved":
        return ApprovalVerification(False, "not-approved")
    integrity = approval.get("integrity")
    if not isinstance(integrity, dict) or integrity.get("algorithm") != "hmac-sha256":
        return ApprovalVerification(False, "untrusted-issuer")
    key_id = integrity.get("keyId")
    signature = integrity.get("signature")
    trusted_secret = trusted_keys.get(key_id) if trusted_keys and isinstance(key_id, str) else None
    if (
        trusted_secret is None
        or not isinstance(signature, str)
        or not signature.startswith("hmac-sha256:")
    ):
        return ApprovalVerification(False, "untrusted-issuer")
    expected_signature = "hmac-sha256:" + hmac.new(
        trusted_secret,
        _approval_signature_payload(approval),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        return ApprovalVerification(False, "invalid-signature")
    if approval.get("proposalId") != proposal.get("proposalId"):
        return ApprovalVerification(False, "proposal-mismatch")
    scopes = approval.get("scope")
    if not isinstance(scopes, list) or required_scope not in scopes:
        return ApprovalVerification(False, "scope-mismatch")
    expected = approval_hash(proposal)
    actual = approval.get("approvedHash")
    if not isinstance(actual, str) or not hmac.compare_digest(actual, expected):
        return ApprovalVerification(False, "hash-mismatch")
    try:
        _parse_timestamp(str(approval["decidedAt"]))
    except (TypeError, ValueError):
        return ApprovalVerification(False, "invalid-decision-time")
    expires_at = approval.get("expiresAt")
    if expires_at is not None:
        try:
            expiry = _parse_timestamp(str(expires_at))
        except (TypeError, ValueError):
            return ApprovalVerification(False, "invalid-expiry")
        effective_now = (now or datetime.now(UTC)).astimezone(UTC)
        if effective_now >= expiry:
            return ApprovalVerification(False, "expired")
    return ApprovalVerification(True, "valid")


def consume_approval(
    approval: Mapping[str, Any],
    proposal: Mapping[str, Any],
    required_scope: str,
    consumption_dir: Path | str,
    *,
    trusted_keys: Mapping[str, bytes] | None = None,
    now: datetime | None = None,
) -> ApprovalVerification:
    verified = verify_approval(
        approval,
        proposal,
        required_scope,
        trusted_keys=trusted_keys,
        now=now,
    )
    if not verified.valid:
        return verified
    approval_id = str(approval["approvalId"])
    token = hashlib.sha256(approval_id.encode("utf-8")).hexdigest()
    destination = Path(consumption_dir) / f"{token}.json"
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        )
    except FileExistsError:
        return ApprovalVerification(False, "already-consumed")
    except OSError:
        return ApprovalVerification(False, "consumption-error")
    receipt = {
        "schemaVersion": "1.0",
        "approvalId": approval_id,
        "proposalId": approval["proposalId"],
        "approvedHash": approval["approvedHash"],
        "scope": required_scope,
        "keyId": approval["integrity"]["keyId"],
        "consumedAt": (now or datetime.now(UTC)).isoformat().replace("+00:00", "Z"),
    }
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(receipt, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        # The exclusive file remains as a fail-closed replay barrier.
        return ApprovalVerification(False, "consumption-error")
    return ApprovalVerification(True, "valid")
