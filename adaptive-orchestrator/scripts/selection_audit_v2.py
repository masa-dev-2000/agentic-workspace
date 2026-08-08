"""Strict, additive v2 selection-audit adapter.

This module deliberately does not modify the v1 selection-audit writer. It
normalizes a body-free request, derives the selected skill from TelemetryStore,
and delegates the atomic write to ``TelemetryStore.record_selection_audit_v2``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from pathlib import Path
from typing import Any

from telemetry_store import TelemetryStore


TOP_LEVEL_FIELDS = {
    "audit_id", "job_id", "session_id", "turn_id", "registry_revision",
    "taxonomy_version", "observation_window_closed", "telemetry_health",
    "runner_terminal", "spool_pending", "cardinality_ok", "candidates",
}
CANDIDATE_FIELDS = {"skill_key", "source", "source_revision", "source_digest", "coverage"}
SOURCE_PROVENANCE = {
    "registry_profile": "registry",
    "planner_candidate": "planner",
    "unfiltered_baseline": "baseline",
}
IDENTITY_RE = re.compile(r"[a-z0-9][a-z0-9._:@+-]*")
RAW_FIELD_NAMES = {
    "body", "content", "prompt", "response", "input", "output", "messages",
    "tool_input", "tool_output", "raw_body", "raw_text", "transcript",
}


def _identity(value: Any, label: str, limit: int = 160) -> str:
    if not isinstance(value, str) or not value or len(value) > limit or not IDENTITY_RE.fullmatch(value):
        raise ValueError(f"invalid {label}")
    return value


def _reject_raw_fields(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str) and key.lower() in RAW_FIELD_NAMES:
                raise ValueError("raw body fields are not allowed")
            _reject_raw_fields(child)
    elif isinstance(value, list):
        for child in value:
            _reject_raw_fields(child)


def _canonical_candidates(candidates: list[dict[str, Any]]) -> str:
    return json.dumps(candidates, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class SelectionAuditV2:
    def __init__(self, store: TelemetryStore | None = None, root: Path | None = None):
        self.store = store or TelemetryStore(root)

    def _observed_skill_keys(self, session_id: str, turn_id: str) -> set[str]:
        session_hash = self.store.pseudonym(session_id, "session")
        turn_hash = self.store.pseudonym(turn_id, "turn")
        rows = self.store.rows(
            """SELECT DISTINCT skill_key FROM skill_runs
               WHERE session_hash=? AND turn_hash=? AND status<>'running'""",
            (session_hash, turn_hash),
        )
        return {str(row["skill_key"]) for row in rows}

    def _expected_audit_id(self, session_id: str, turn_id: str) -> str:
        session_hash = self.store.pseudonym(session_id, "session")
        turn_hash = self.store.pseudonym(turn_id, "turn")
        return "selectionaudit_" + hmac.new(
            self.store._secret(),
            f"audit:{session_hash}:{turn_hash}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:32]

    def record(self, payload: dict[str, Any]) -> dict[str, Any]:
        _reject_raw_fields(payload)
        if not isinstance(payload, dict) or set(payload) != TOP_LEVEL_FIELDS:
            raise ValueError("invalid selection audit v2 fields")
        audit_id = _identity(payload["audit_id"], "audit id")
        job_id = _identity(payload["job_id"], "job id")
        session_id = _identity(payload["session_id"], "session id")
        turn_id = _identity(payload["turn_id"], "turn id")
        registry_revision = _identity(payload["registry_revision"], "registry revision")
        taxonomy_version = _identity(payload["taxonomy_version"], "taxonomy version")
        for name in ("observation_window_closed", "runner_terminal", "cardinality_ok"):
            if not isinstance(payload[name], bool):
                raise ValueError("selection audit completeness flags must be boolean")
        if payload["telemetry_health"] not in {"complete", "degraded", "failed"}:
            raise ValueError("invalid telemetry health")
        spool_pending = payload["spool_pending"]
        if isinstance(spool_pending, bool) or not isinstance(spool_pending, int) or spool_pending < 0:
            raise ValueError("invalid spool_pending")
        if not isinstance(payload["candidates"], list) or not payload["candidates"]:
            raise ValueError("selection audit v2 candidates are required")
        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in payload["candidates"]:
            if not isinstance(item, dict) or set(item) != CANDIDATE_FIELDS:
                raise ValueError("invalid selection audit v2 candidate fields")
            normalized = {
                "skill_key": _identity(item["skill_key"], "skill key"),
                "source": item["source"],
                "source_revision": _identity(item["source_revision"], "source revision"),
                "source_digest": item["source_digest"],
                "coverage": item["coverage"],
            }
            if normalized["skill_key"] in seen:
                raise ValueError("duplicate candidate skill key")
            seen.add(normalized["skill_key"])
            if normalized["source"] not in SOURCE_PROVENANCE:
                raise ValueError("invalid candidate source")
            if normalized["coverage"] not in {"known", "unknown"}:
                raise ValueError("invalid candidate coverage")
            if not isinstance(normalized["source_digest"], str) or not re.fullmatch(r"[0-9a-f]{64}", normalized["source_digest"]):
                raise ValueError("invalid source digest")
            candidates.append(normalized)
        expected_audit_id = self._expected_audit_id(session_id, turn_id)
        if not hmac.compare_digest(audit_id, expected_audit_id):
            raise ValueError("audit id HMAC mismatch")
        observed = self._observed_skill_keys(session_id, turn_id)
        if len(observed) > 1:
            raise ValueError("multi-observed skill conflict")
        observation_complete = (
            payload["observation_window_closed"]
            and payload["runner_terminal"]
            and payload["telemetry_health"] == "complete"
            and payload["spool_pending"] == 0
            and payload["cardinality_ok"]
        )
        observation_state = "complete" if observation_complete else "incomplete"
        if payload["telemetry_health"] == "failed":
            observation_state = "failed"
        selected = next(iter(observed), None)
        persisted_candidates: list[dict[str, Any]] = []
        for item in candidates:
            if not observation_complete:
                classification, reason = "not_observable", "observation_incomplete"
            elif selected == item["skill_key"]:
                classification, reason = "selected", "observed_skill"
            elif item["coverage"] == "unknown":
                classification, reason = "candidate_coverage_unknown", "candidate_coverage_unknown"
            else:
                classification, reason = "not_comparable", "not_observed"
            persisted_candidates.append({
                **item,
                "provenance": SOURCE_PROVENANCE[item["source"]],
                "classification": classification,
                "reason_code": reason,
            })
        candidate_digest = hashlib.sha256(
            _canonical_candidates(candidates).encode("utf-8")
        ).hexdigest()
        return self.store.record_selection_audit_v2({
            "audit_id": audit_id,
            "job_id": job_id,
            "session_id": session_id,
            "turn_id": turn_id,
            "registry_revision": registry_revision,
            "taxonomy_version": taxonomy_version,
            "observation_state": observation_state,
            "observation_window_closed": payload["observation_window_closed"],
            "telemetry_health": payload["telemetry_health"],
            "runner_terminal": payload["runner_terminal"],
            "spool_pending": spool_pending,
            "cardinality_ok": payload["cardinality_ok"],
            "candidate_digest": candidate_digest,
            "candidates": persisted_candidates,
        })


def record_selection_audit_v2(payload: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    return SelectionAuditV2(root=root).record(payload)

