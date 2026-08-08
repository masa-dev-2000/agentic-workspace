#!/usr/bin/env python3
"""Deterministic policy and body-free event helper for adaptive-orchestrator."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import sys
import time
import uuid
from pathlib import Path

try:
    from agent_router import RoutingError, route_tasks
except ImportError:  # pragma: no cover - package/CLI compatibility
    _router_spec = importlib.util.spec_from_file_location("adaptive_agent_router", Path(__file__).with_name("agent_router.py"))
    if _router_spec is None or _router_spec.loader is None:
        raise
    _router_module = importlib.util.module_from_spec(_router_spec)
    sys.modules["adaptive_agent_router"] = _router_module
    _router_spec.loader.exec_module(_router_module)
    RoutingError, route_tasks = _router_module.RoutingError, _router_module.route_tasks

try:
    from skill_entry_router import EntryRoutingError, route_request
except ImportError:  # pragma: no cover - package/CLI compatibility
    _entry_spec = importlib.util.spec_from_file_location("adaptive_skill_entry_router", Path(__file__).with_name("skill_entry_router.py"))
    if _entry_spec is None or _entry_spec.loader is None:
        raise
    _entry_module = importlib.util.module_from_spec(_entry_spec)
    sys.modules["adaptive_skill_entry_router"] = _entry_module
    _entry_spec.loader.exec_module(_entry_module)
    EntryRoutingError, route_request = _entry_module.EntryRoutingError, _entry_module.route_request

try:
    from entry_rollout import RolloutError, decide, evidence as rollout_evidence, initial_state, transition
except ImportError:  # pragma: no cover - package/CLI compatibility
    _rollout_spec = importlib.util.spec_from_file_location("adaptive_entry_rollout", Path(__file__).with_name("entry_rollout.py"))
    if _rollout_spec is None or _rollout_spec.loader is None:
        raise
    _rollout_module = importlib.util.module_from_spec(_rollout_spec)
    sys.modules["adaptive_entry_rollout"] = _rollout_module
    _rollout_spec.loader.exec_module(_rollout_module)
    RolloutError = _rollout_module.RolloutError
    decide, rollout_evidence, initial_state, transition = (_rollout_module.decide, _rollout_module.evidence, _rollout_module.initial_state, _rollout_module.transition)

FATAL_CLASSES = {
    "production-change", "destructive-delete", "external-publish", "external-send",
    "financial", "legal-contract", "external-service-critical-config",
    "sensitive-data-external", "privilege-change",
}

def load_json(value: str) -> dict:
    if value.startswith("@"):
        return json.loads(Path(value[1:]).read_text(encoding="utf-8"))
    return json.loads(value)

def digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def policy(payload: dict) -> dict:
    actions = payload.get("actions") or []
    unknown, fatal_actions = [], []
    for action in actions:
        classes = set(action.get("side_effect_classes") or [])
        if not classes or "unknown" in classes:
            unknown.append(action.get("action_id", "unknown"))
        fatal = classes & FATAL_CLASSES
        if fatal:
            fatal_actions.append({"action_id": action.get("action_id"), "classes": sorted(fatal)})
    if unknown:
        decision, reason = "require_approval", "side-effect classification is missing or unknown"
    elif fatal_actions:
        decision, reason = "require_approval", "fatal side effect requires human approval"
    else:
        decision, reason = "allow", "allowlisted non-fatal side effects"
    return {
        "decision": decision, "reason": reason, "fatal_actions": fatal_actions,
        "unknown_actions": unknown, "policy_version": "adaptive-orchestrator-policy-v1",
        "plan_digest": digest(payload), "evaluated_at": time.time(),
    }

def plan(payload: dict) -> dict:
    goal, tasks = payload.get("goal", ""), payload.get("tasks") or []
    if not goal or not tasks:
        raise ValueError("goal and tasks are required")
    for index, task in enumerate(tasks):
        task.setdefault("task_id", f"task-{index + 1}")
        task.setdefault("objective", task.get("title", ""))
        task.setdefault("status", "ready")
        task.setdefault("dependencies", [])
        task.setdefault("retry_limit", 2)
        task.setdefault("write_scope", [])
        task.setdefault("acceptance_criteria", [])
        task.setdefault("side_effect_classes", ["unknown"])
        task.setdefault("role", "unassigned")
        task.setdefault("model_provider", "unavailable")
        task.setdefault("verification", {"required": True})
        task.setdefault("fallback", ["retry", "replan"])
        if not task["objective"]:
            raise ValueError(f"task {task['task_id']} requires objective")
    root_free_mode = bool(payload.get("root_free_mode")) or payload.get("execution_mode") == "root_free_mode"
    mode = payload.get("execution_mode")
    if root_free_mode:
        mode = "root_free_mode"
    elif mode not in {"direct", "handoff", "team"}:
        mode = "team" if len(tasks) > 1 else "direct"
    selection_audit = payload.get("selection_audit")
    if selection_audit is not None:
        if not isinstance(selection_audit, dict):
            raise ValueError("selection_audit must be an object")
        for key in ("registry_revision", "taxonomy_version", "intent_category"):
            if not isinstance(selection_audit.get(key), str) or not selection_audit[key] or len(selection_audit[key]) > 128:
                raise ValueError(f"selection_audit requires {key}")
        candidates = selection_audit.get("planner_candidates") or []
        if not isinstance(candidates, list) or len(candidates) > 256 or any(not isinstance(value, str) or not value for value in candidates):
            raise ValueError("invalid planner_candidates")
    result = {
        "schema": "orchestration_plan_v2" if selection_audit is not None else "orchestration_plan_v1", "job_id": payload.get("job_id", f"job-{uuid.uuid4().hex}"),
        "session_id": payload.get("session_id", "unavailable"), "goal": goal,
        "execution_mode": mode, "tasks": tasks,
        "budget": payload.get("budget", {"tokens": "unavailable", "seconds": "unavailable"}),
        "verification": payload.get("verification", {"required": True}),
        "fallback": payload.get("fallback", ["retry", "handoff", "replan"]),
        "approval_requirement": "policy-engine",
        "root_free_mode": root_free_mode,
    }
    if root_free_mode:
        try:
            result["routing"] = route_tasks(tasks)
        except RoutingError:
            raise
    else:
        result["routing"] = {"status": "not_applicable"}
    if selection_audit is not None:
        result["selection_audit"] = {"registry_revision": selection_audit["registry_revision"], "taxonomy_version": selection_audit["taxonomy_version"], "intent_category": selection_audit["intent_category"], "planner_candidates": sorted(set(selection_audit.get("planner_candidates") or []))}
    result["plan_digest"] = digest(result)
    return result

def entry(payload: dict) -> dict:
    """Return a body-free Skill entry and rollout decision."""
    request = payload.get("request", payload)
    if not isinstance(request, dict):
        raise ValueError("entry request must be an object")
    route = route_request(request)
    state = payload.get("rollout_state") or initial_state()
    decision = decide(state, route.skill, payload.get("rollout_request") or request)
    evidence = rollout_evidence(state, decision, job_id=str(payload.get("job_id", "unavailable")), attempt_id=str(payload.get("attempt_id", "unavailable")), event_id=str(payload.get("event_id", "unavailable")))
    return {
        "schema": "skill_entry_rollout_v1",
        "entrypoint": route.entrypoint,
        "skill": route.skill,
        "reason": route.reason,
        "route_depth": route.depth,
        "rollout": decision,
        "rollout_evidence": evidence,
        "shadow": state["mode"] == "shadow",
        "legacy_execution_unchanged": True,
    }

def entry_transition(payload: dict) -> dict:
    state = payload.get("state")
    if not isinstance(state, dict):
        raise ValueError("state is required")
    result = transition(state, payload.get("target_mode"), expected_state_version=payload.get("expected_state_version"), route_digest=payload.get("route_digest", "unavailable"), policy_digest=payload.get("policy_digest", "unavailable"), evidence_ready=bool(payload.get("evidence_ready")), compatibility_ok=bool(payload.get("compatibility_ok")), compensation_complete=bool(payload.get("compensation_complete")), reason=payload.get("reason", ""))
    return {"schema": "skill_entry_rollout_transition_v1", **result}

def event(payload: dict) -> dict:
    allowed = {
        "schema", "job_id", "session_id", "turn_id", "run_id", "task_id", "status",
        "execution_mode", "role", "model_class", "start_time", "end_time", "wait_seconds",
        "input_tokens", "output_tokens", "total_tokens", "cost", "tool_count", "failure_count",
        "handoff_count", "retry_count", "rework", "human_intervention", "quality", "evidence_ref",
        "agent_id", "authority", "route_reason", "root_return_reason", "root_return_state", "registry_revision",
        "entrypoint", "route_depth", "rollout_mode", "rollout_status", "state_version", "rollback_count",
    }
    result = {key: value for key, value in payload.items() if key in allowed}
    result.setdefault("schema", "orchestration_event_v1")
    result.setdefault("status", "unavailable")
    result["body_free"] = True
    result["event_digest"] = digest(result)
    return result

def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "policy", "event", "entry", "entry-transition"):
        item = sub.add_parser(name)
        item.add_argument("payload", help="JSON object or @path-to-json")
    args = parser.parse_args()
    try:
        payload = load_json(args.payload)
        result = {"plan": plan, "policy": policy, "event": event, "entry": entry, "entry-transition": entry_transition}[args.command](payload)
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
        return 0
    except (ValueError, KeyError, json.JSONDecodeError, OSError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
