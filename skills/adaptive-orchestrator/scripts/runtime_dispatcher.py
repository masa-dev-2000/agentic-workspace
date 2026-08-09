"""Thin orchestration dispatcher over an existing Sub Agent backend.

The backend owns model/agent execution. This module owns only DAG ordering,
idempotency, bounded fallback, result collection, and Root return decisions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
import hashlib
import json


TERMINAL = {"succeeded", "failed", "cancelled", "unknown", "approval-required"}
RETRYABLE = {"transient", "timeout", "lease-expired"}


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DispatchEnvelope:
    dispatch_id: str
    run_id: str
    node_id: str
    attempt: int
    status: str
    retryable: bool = False
    approval_required: bool = False
    result: Any = None
    error_class: str | None = None
    evidence_ref: str | None = None

    def __post_init__(self) -> None:
        if not self.dispatch_id or not self.run_id or not self.node_id or self.attempt < 1:
            raise ValueError("dispatch identity is required")
        if self.status not in {"accepted", "running", *TERMINAL}:
            raise ValueError("invalid dispatch status")
        if self.approval_required and self.status != "approval-required":
            raise ValueError("approval_required must use approval-required status")


class Backend(Protocol):
    def start(self, request: dict[str, Any]) -> DispatchEnvelope: ...
    def wait(self, dispatch_id: str) -> DispatchEnvelope: ...


class MultiAgentBackendAdapter:
    """Adapter for parent-runtime wrappers around multi_agent_v1 tools.

    The MCP tool itself is owned by the parent runtime; wrappers translate its
    response into the strict envelope used here.
    """
    def __init__(self, spawn_agent: Any, wait_agent: Any):
        self.spawn_agent = spawn_agent
        self.wait_agent = wait_agent

    @staticmethod
    def _envelope(raw: Any, request: dict[str, Any], dispatch_id: str | None = None) -> DispatchEnvelope:
        if not isinstance(raw, dict):
            raise RuntimeError("backend returned non-object envelope")
        # The parent Runtime wrapper may return only an agent_id on spawn.
        status = raw.get("status", "accepted" if raw.get("agent_id") else "unknown")
        if status == "completed":
            status = "succeeded"
        if status not in {"accepted", "running", *TERMINAL}:
            status = "unknown"
        return DispatchEnvelope(
            dispatch_id or raw.get("dispatch_id") or raw.get("agent_id", f"dispatch-{request['node_id']}-{request['attempt']}"),
            request["run_id"], request["node_id"], request["attempt"], status,
            bool(raw.get("retryable", False)), bool(raw.get("approval_required", False)),
            raw.get("result"), raw.get("error_class"), raw.get("evidence_ref", "unavailable"),
        )

    def start(self, request: dict[str, Any]) -> DispatchEnvelope:
        raw = self.spawn_agent(request)
        return self._envelope(raw, request)

    def wait(self, dispatch_id: str) -> DispatchEnvelope:
        raw = self.wait_agent(dispatch_id)
        if not isinstance(raw, dict):
            raise RuntimeError("backend returned non-object envelope")
        return self._envelope(raw, {"run_id": raw.get("run_id", "unavailable"), "node_id": raw.get("node_id", "unavailable"), "attempt": int(raw.get("attempt", 1))}, dispatch_id)


@dataclass
class DispatchOutcome:
    state: str
    root_return: bool
    reason: str
    next_action: str
    approval_status: str
    results: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)


class Dispatcher:
    def __init__(self, backend: Backend, *, max_fallbacks: int = 2, max_total_attempts: int = 12):
        self.backend = backend
        self.max_fallbacks = max_fallbacks
        self.max_total_attempts = max_total_attempts
        self._started: dict[str, DispatchEnvelope] = {}

    def _evidence(self, envelope: DispatchEnvelope, route: dict[str, Any]) -> dict[str, Any]:
        return {"event_id": _digest([envelope.dispatch_id, envelope.attempt, envelope.status]), "run_id": envelope.run_id, "node_id": envelope.node_id, "attempt": envelope.attempt, "transition": envelope.status, "backend_class": route.get("backend_class", "multi_agent_v1"), "status": envelope.status, "evidence_ref": envelope.evidence_ref or "unavailable"}

    def run(self, *, run_id: str, tasks: list[dict[str, Any]], routes: list[dict[str, Any]], approval_context: dict[str, Any] | None = None) -> DispatchOutcome:
        by_id = {task["task_id"]: task for task in tasks}
        route_by_id = {route["task_id"]: route for route in routes}
        if set(by_id) != set(route_by_id):
            return DispatchOutcome("BLOCKED_POLICY", True, "route_task_mismatch", "return_root", "none")
        states: dict[str, str] = {task_id: "pending" for task_id in by_id}
        results: dict[str, Any] = {}
        evidence: list[dict[str, Any]] = []
        fallback_counts: dict[str, int] = {task_id: 0 for task_id in by_id}
        attempts_total = 0
        for task in tasks:
            task_id = task["task_id"]
            dependencies = task.get("dependencies", [])
            if any(dependency not in by_id for dependency in dependencies):
                return DispatchOutcome("FAILED_CYCLE", True, "unknown_dependency", "return_root", "none", results, evidence)
            if any(states[dependency] != "succeeded" for dependency in dependencies):
                return DispatchOutcome("BLOCKED_POLICY", True, "dependency_not_succeeded", "return_root", "none", results, evidence)
            route = dict(route_by_id[task_id])
            attempt = 1
            while True:
                if attempts_total >= self.max_total_attempts:
                    return DispatchOutcome("FAILED_BUDGET", True, "total_attempt_limit", "return_root", "none", results, evidence)
                attempts_total += 1
                key = f"{run_id}:{task_id}:{attempt}"
                if key in self._started:
                    envelope = self._started[key]
                else:
                    approval_required = bool(route.get("approval_required") or task.get("approval_requested"))
                    if approval_required and not (approval_context or {}).get("approved"):
                        return DispatchOutcome("BLOCKED_APPROVAL", True, "human_approval_required", "return_root", "approval_requested", results, evidence)
                    request = {"run_id": run_id, "node_id": task_id, "attempt": attempt, "idempotency_key": key, "agent_id": route["agent_id"], "model": route.get("runtime_model_id", route["model"]), "model_key": route.get("model_key", route["model"]), "provider": route.get("provider", "unavailable"), "authority": route["authority"], "write_scope": task.get("write_scope", []), "approval_status": "approved" if approval_required else "none"}
                    try:
                        envelope = self.backend.start(request)
                    except Exception:
                        return DispatchOutcome("RETURN_ROOT", True, "backend_unavailable", "return_root", "none", results, evidence)
                    if envelope.run_id != run_id or envelope.node_id != task_id or envelope.attempt != attempt:
                        return DispatchOutcome("RETURN_ROOT", True, "dispatch_identity_mismatch", "return_root", "none", results, evidence)
                    self._started[key] = envelope
                evidence.append(self._evidence(envelope, route))
                if envelope.status in {"accepted", "running"}:
                    try:
                        envelope = self.backend.wait(envelope.dispatch_id)
                    except Exception:
                        return DispatchOutcome("RETURN_ROOT", True, "backend_unavailable", "return_root", "none", results, evidence)
                    if envelope.run_id != run_id or envelope.node_id != task_id or envelope.attempt != attempt:
                        return DispatchOutcome("RETURN_ROOT", True, "dispatch_identity_mismatch", "return_root", "none", results, evidence)
                    evidence.append(self._evidence(envelope, route))
                if envelope.status == "succeeded":
                    states[task_id] = "succeeded"; results[task_id] = envelope.result
                    break
                if envelope.status == "approval-required" or envelope.approval_required:
                    return DispatchOutcome("BLOCKED_APPROVAL", True, "human_approval_required", "return_root", "approval_requested", results, evidence)
                if envelope.status == "unknown":
                    return DispatchOutcome("RETURN_ROOT", True, "execution_state_unknown", "return_root", "none", results, evidence)
                if envelope.status in {"failed", "cancelled"}:
                    if not envelope.retryable or envelope.error_class not in RETRYABLE or fallback_counts[task_id] >= self.max_fallbacks:
                        return DispatchOutcome("FAILED_RETRY_EXHAUSTED", True, envelope.error_class or envelope.status, "return_root", "none", results, evidence)
                    fallback_counts[task_id] += 1
                    fallbacks = route.get("fallback_agents", [])
                    if not fallbacks:
                        return DispatchOutcome("FAILED_RETRY_EXHAUSTED", True, "no_safe_fallback", "return_root", "none", results, evidence)
                    fallback_routes = route.get("fallback_routes", [])
                    if fallback_routes:
                        if fallback_counts[task_id] > len(fallback_routes):
                            return DispatchOutcome("FAILED_RETRY_EXHAUSTED", True, "no_safe_fallback", "return_root", "none", results, evidence)
                        route = dict(fallback_routes[fallback_counts[task_id] - 1])
                    else:
                        route["agent_id"] = fallbacks[0]
                    attempt += 1
        return DispatchOutcome("COMPLETED", True, "all_subagents_completed", "integrate_and_report", "none", results, evidence)
