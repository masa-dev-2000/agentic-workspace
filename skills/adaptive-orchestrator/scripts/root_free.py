"""Root return state machine and bounded fallback rules."""
from __future__ import annotations

from typing import Any

STATES = {"RUNNING", "WAITING_CHILDREN", "COMPLETED", "RETURN_ROOT", "BLOCKED_APPROVAL", "BLOCKED_POLICY", "FAILED_RETRY_EXHAUSTED", "FAILED_TIMEOUT", "FAILED_CYCLE", "FAILED_BUDGET"}
RETRYABLE = {"transient", "lease-expired", "repairable"}
CRITICAL = {"production-change", "destructive-delete", "external-publish", "external-send", "financial", "legal-contract", "external-service-critical-config", "sensitive-data-external", "privilege-change"}


class RootFreeError(ValueError):
    pass


def _evaluate(event: dict[str, Any]) -> dict[str, Any]:
    status = event.get("status", "unknown")
    if status not in {"running", "waiting", "completed", "failed", "blocked", "approval_requested"}:
        return {"state": "BLOCKED_POLICY", "root_return": True, "reason": "unknown_state"}
    if event.get("approval_requested") or event.get("side_effect_class") in CRITICAL:
        if event.get("approved") is not True:
            return {"state": "BLOCKED_APPROVAL", "root_return": True, "reason": "human_approval_required", "approval_status": "approval_requested"}
    if event.get("policy_decision") in {"deny", "blocked"}:
        return {"state": "BLOCKED_POLICY", "root_return": True, "reason": "policy_denied"}
    if event.get("cycle_detected"):
        return {"state": "FAILED_CYCLE", "root_return": True, "reason": "dependency_cycle"}
    if event.get("budget_exceeded"):
        return {"state": "FAILED_BUDGET", "root_return": True, "reason": "budget_exceeded"}
    if event.get("time_exceeded"):
        return {"state": "FAILED_TIMEOUT", "root_return": True, "reason": "time_limit_exceeded"}
    if event.get("verification_match") is False:
        return {"state": "RETURN_ROOT", "root_return": True, "reason": "verification_mismatch"}
    if event.get("user_decision_required"):
        return {"state": "RETURN_ROOT", "root_return": True, "reason": "user_decision_required"}
    if status == "completed":
        return {"state": "COMPLETED", "root_return": True, "reason": "completed_for_integration", "approval_status": "none"}
    if status == "failed":
        attempts = int(event.get("attempt", 0)); max_attempts = int(event.get("max_attempts", 0))
        if event.get("failure_class") in RETRYABLE and attempts < max_attempts:
            return {"state": "WAITING_CHILDREN", "root_return": False, "reason": "bounded_retry"}
        return {"state": "FAILED_RETRY_EXHAUSTED", "root_return": True, "reason": "retry_exhausted"}
    if status == "blocked":
        return {"state": "BLOCKED_POLICY", "root_return": True, "reason": "blocked"}
    return {"state": "WAITING_CHILDREN", "root_return": False, "reason": "subagent_running"}


def evaluate(event: dict[str, Any]) -> dict[str, Any]:
    result = _evaluate(event)
    result.setdefault("next_action", "return_root" if result["root_return"] else "wait_for_subagent")
    result.setdefault("human_approval", result.get("approval_status") == "approval_requested")
    result.setdefault("approval_status", "none")
    return result


def choose_fallback(route: dict[str, Any], failed_agent: str, failure_class: str, fallback_count: int) -> dict[str, Any]:
    if failure_class not in RETRYABLE or fallback_count >= 2:
        return {"action": "return_root", "reason": "fallback_limit_or_non_retryable_failure"}
    for candidate in route.get("fallback_agents", []):
        if candidate == failed_agent:
            continue
        if candidate not in route.get("allowed_fallback_agents", route.get("fallback_agents", [])):
            continue
        if route.get("candidate_risk_level") and route["candidate_risk_level"] > route.get("risk_level", "low"):
            continue
        return {"action": "handoff", "agent_id": candidate, "reason": "bounded_alternate_agent"}
    return {"action": "return_root", "reason": "no_safe_fallback"}
