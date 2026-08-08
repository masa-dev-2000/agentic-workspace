"""Fail-closed local rollout state machine for Skill entry routing."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from typing import Any

MODES = {"shadow", "canary", "cutover", "legacy"}
ALLOWED_EFFECTS = {"local-reversible"}
FORBIDDEN_EFFECTS = {
    "external-io", "external-send", "external-publish", "production-change",
    "destructive-delete", "financial", "legal-contract", "privilege-change",
    "secret-access", "auth-state-access",
}


class RolloutError(ValueError):
    pass


@dataclass(frozen=True)
class RolloutState:
    mode: str = "shadow"
    state_version: int = 0
    revision: str = "entry-router-v1"
    policy_digest: str = "unavailable"
    route_digest: str = "unavailable"
    rollback_count: int = 0


def digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def initial_state(**overrides: Any) -> dict[str, Any]:
    state = asdict(RolloutState(**overrides))
    validate_state(state)
    return state


def validate_state(state: dict[str, Any]) -> None:
    if not isinstance(state, dict) or state.get("mode") not in MODES:
        raise RolloutError("rollout mode is unknown")
    if not isinstance(state.get("state_version"), int) or state["state_version"] < 0:
        raise RolloutError("state_version is invalid")
    if not isinstance(state.get("revision"), str) or not state["revision"]:
        raise RolloutError("rollout revision is invalid")
    if not isinstance(state.get("rollback_count"), int) or state["rollback_count"] < 0:
        raise RolloutError("rollback_count is invalid")


def classify_candidate(request: dict[str, Any]) -> str:
    required = ("side_effect_classes", "risk_level", "external_io", "secret_access", "auth_state_access", "compensation_defined")
    if any(key not in request for key in required):
        return "unknown"
    classes = request["side_effect_classes"]
    if not isinstance(classes, list) or not classes or any(not isinstance(value, str) for value in classes):
        return "unknown"
    if any(value in FORBIDDEN_EFFECTS for value in classes):
        return "unsafe"
    if set(classes) != ALLOWED_EFFECTS:
        return "unknown"
    if request["risk_level"] != "low":
        return "unsafe" if request["risk_level"] in {"medium", "high", "critical"} else "unknown"
    booleans = ("external_io", "secret_access", "auth_state_access", "compensation_defined")
    if any(not isinstance(request[key], bool) for key in booleans):
        return "unknown"
    if request["external_io"] or request["secret_access"] or request["auth_state_access"]:
        return "unsafe"
    if not request["compensation_defined"]:
        return "unknown"
    return "allowed"


def transition(state: dict[str, Any], target_mode: str, *, expected_state_version: int,
               route_digest: str = "unavailable", policy_digest: str = "unavailable",
               evidence_ready: bool = False, compatibility_ok: bool = False,
               compensation_complete: bool = False, reason: str = "") -> dict[str, Any]:
    validate_state(state)
    if target_mode not in MODES:
        raise RolloutError("target rollout mode is unknown")
    if expected_state_version != state["state_version"]:
        raise RolloutError("rollout state version mismatch")
    current = state["mode"]
    if target_mode == current:
        return {"state": dict(state), "changed": False, "reason": "idempotent_noop"}
    allowed = {
        "shadow": {"canary", "legacy"},
        "canary": {"cutover", "legacy"},
        "cutover": {"legacy"},
        "legacy": {"shadow"},
    }
    if target_mode not in allowed[current]:
        raise RolloutError(f"invalid rollout transition {current}->{target_mode}")
    if target_mode in {"canary", "cutover"}:
        if not evidence_ready or not compatibility_ok:
            raise RolloutError("rollout evidence or compatibility gate is incomplete")
        if not route_digest or route_digest == "unavailable" or not policy_digest or policy_digest == "unavailable":
            raise RolloutError("route and policy digests are required")
    if target_mode == "legacy":
        if not reason:
            raise RolloutError("rollback reason is required")
        if not compensation_complete:
            raise RolloutError("rollback compensation is incomplete")
    next_state = dict(state)
    next_state["mode"] = target_mode
    next_state["state_version"] += 1
    next_state["route_digest"] = route_digest
    next_state["policy_digest"] = policy_digest
    if current != "legacy" and target_mode == "legacy":
        next_state["rollback_count"] += 1
    validate_state(next_state)
    return {"state": next_state, "changed": True, "reason": reason or f"transition_{current}_to_{target_mode}"}


def decide(state: dict[str, Any], candidate_skill: str | None, request: dict[str, Any]) -> dict[str, Any]:
    validate_state(state)
    if not candidate_skill:
        raise RolloutError("candidate route is unavailable")
    classification = classify_candidate(request)
    if state["mode"] == "shadow":
        selected = "legacy"
        status = "shadow"
        reason = "shadow_does_not_execute_candidate"
    elif state["mode"] == "canary":
        if classification == "allowed":
            selected, status, reason = "candidate", "canary_allowed", "deterministic_allowlist_match"
        elif classification == "unsafe":
            selected, status, reason = "legacy", "canary_fallback", "candidate_outside_allowlist"
        else:
            selected, status, reason = "none", "blocked_unknown", "candidate_classification_unknown"
    elif state["mode"] == "cutover":
        if classification != "allowed":
            return {"selected": "none", "status": "blocked_unknown", "reason": "cutover_candidate_not_allowed", "classification": classification}
        selected, status, reason = "candidate", "cutover_allowed", "cutover_state_observed"
    else:
        selected, status, reason = "legacy", "legacy_mode", "legacy_rollout_mode"
    return {"selected": selected, "status": status, "reason": reason, "classification": classification}


def evidence(state: dict[str, Any], decision: dict[str, Any], *, job_id: str,
             attempt_id: str, event_id: str) -> dict[str, Any]:
    validate_state(state)
    if not all(isinstance(value, str) and value for value in (job_id, attempt_id, event_id)):
        raise RolloutError("evidence identifiers are required")
    result = {
        "schema": "skill_entry_rollout_evidence_v1", "event_id": event_id,
        "job_id": job_id, "attempt_id": attempt_id, "mode": state["mode"],
        "state_version": state["state_version"], "route_digest": state["route_digest"],
        "policy_digest": state["policy_digest"], "rollback_count": state["rollback_count"],
        "status": decision["status"], "reason": decision["reason"],
        "classification": decision.get("classification", "unknown"), "body_free": True,
    }
    result["evidence_digest"] = digest(result)
    return result
