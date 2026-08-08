"""Fail-closed, body-free Agent Registry routing for root_free_mode."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from model_catalog import load_catalog, resolve_model

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "references" / "agent-registry.json"
POLICY_PATH = ROOT / "references" / "agent-policy.json"
RISK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


class RoutingError(ValueError):
    pass


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def load_json(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RoutingError("registry unavailable or invalid") from exc
    validate_registry(data)
    return data


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RoutingError("policy unavailable or invalid") from exc
    if data.get("schema") != "agent_policy_v1" or not isinstance(data.get("agents"), dict):
        raise RoutingError("policy schema invalid")
    return data


def validate_registry(data: dict[str, Any]) -> None:
    required = {"id", "role", "model", "capabilities", "authority", "risk_level", "preferred_task_types", "fallback_agents", "verification_required"}
    if data.get("schema") != "agent_registry_v1" or not isinstance(data.get("revision"), str) or not data["revision"]:
        raise RoutingError("registry schema or revision invalid")
    agents = data.get("agents")
    if not isinstance(agents, list) or not agents:
        raise RoutingError("registry agents missing")
    ids: set[str] = set()
    for agent in agents:
        if not isinstance(agent, dict) or not required <= set(agent):
            raise RoutingError("agent registry entry incomplete")
        identifier = agent["id"]
        if not isinstance(identifier, str) or not identifier or identifier in ids:
            raise RoutingError("duplicate or invalid agent id")
        ids.add(identifier)
        if agent["risk_level"] not in RISK or not isinstance(agent["model"], str) or not agent["model"]:
            raise RoutingError("agent model or risk invalid")
        for field in ("capabilities", "authority", "preferred_task_types", "fallback_agents"):
            if not isinstance(agent[field], list) or any(not isinstance(item, str) or not item for item in agent[field]):
                raise RoutingError(f"agent {field} invalid")
        if any("path" in key.lower() or "command" in key.lower() for key in agent):
            raise RoutingError("registry cannot contain executable paths or commands")
    for agent in agents:
        if any(fallback not in ids for fallback in agent["fallback_agents"]):
            raise RoutingError("fallback agent is not registered")


def _topological_order(tasks: list[dict[str, Any]]) -> tuple[list[str], list[list[str]]]:
    ids = [task["task_id"] for task in tasks]
    if len(set(ids)) != len(ids):
        raise RoutingError("duplicate task id")
    by_id = {task["task_id"]: task for task in tasks}
    indegree = {identifier: 0 for identifier in ids}
    children = {identifier: [] for identifier in ids}
    for task in tasks:
        for dependency in task.get("dependencies", []):
            if dependency not in by_id:
                raise RoutingError("orphan task dependency")
            indegree[task["task_id"]] += 1
            children[dependency].append(task["task_id"])
    order: list[str] = []
    groups: list[list[str]] = []
    ready = sorted(identifier for identifier, count in indegree.items() if count == 0)
    while ready:
        groups.append(ready[:])
        order.extend(ready)
        next_ready: list[str] = []
        for identifier in ready:
            for child in children[identifier]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    next_ready.append(child)
        ready = sorted(next_ready)
    if len(order) != len(tasks):
        raise RoutingError("task dependency cycle")
    return order, groups


def route_task(task: dict[str, Any], registry: dict[str, Any] | None = None, policy: dict[str, Any] | None = None, model_catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    registry = registry or load_json()
    policy = policy or load_policy()
    model_catalog = model_catalog or load_catalog()
    required_capabilities = set(task.get("required_capabilities") or [])
    required_authority = set(task.get("required_authority") or ["read"])
    task_type = task.get("task_type") or task.get("role")
    if not isinstance(task_type, str) or not task_type:
        raise RoutingError("root_free task_type is required")
    requested = task.get("preferred_agent")
    if requested and not any(agent["id"] == requested for agent in registry["agents"]):
        raise RoutingError("preferred agent is unknown")
    task_risk = task.get("risk_level", "low")
    if task_risk not in RISK:
        raise RoutingError("task risk is unknown")
    candidates = []
    for agent in registry["agents"]:
        if not agent.get("enabled", True) or (requested and agent["id"] != requested):
            continue
        agent_policy = policy["agents"].get(agent["id"])
        if not agent_policy or RISK[agent["risk_level"]] < RISK[task_risk] or not required_capabilities <= set(agent["capabilities"]):
            continue
        if not required_authority <= set(agent["authority"]):
            continue
        if not required_authority <= set(agent_policy["allowed_operations"]):
            continue
        if RISK[task_risk] > RISK[agent_policy["max_risk"]]:
            continue
        preferred = 1 if task_type in agent["preferred_task_types"] else 0
        model_match = 1 if task.get("model_preference") == agent["model"] else 0
        candidates.append((preferred, model_match, -int(agent.get("estimated_seconds", 10**9)), agent.get("estimated_cost", "unavailable"), agent["id"], agent))
    if not candidates:
        raise RoutingError("no agent satisfies capability, authority, and risk policy")
    candidates.sort(key=lambda item: (-item[0], -item[1], item[2], item[3], item[4]))
    agent = candidates[0][-1]
    agent_policy = policy["agents"][agent["id"]]
    model = resolve_model(agent["model"], model_catalog)
    return {
        "task_id": task["task_id"], "agent_id": agent["id"], "role": agent["role"], "model": agent["model"],
        **model,
        "authority": sorted(required_authority), "risk_level": task_risk,
        "selection_reason": "capability_authority_risk_match" + ("_task_type_preference" if task_type in agent["preferred_task_types"] else ""),
        "fallback_agents": agent["fallback_agents"], "estimated_seconds": agent.get("estimated_seconds", "unavailable"),
        "estimated_cost": agent.get("estimated_cost", "unavailable"), "verification_required": bool(agent["verification_required"]),
        "policy_revision": policy["revision"], "registry_revision": registry["revision"],
        "approval_required": bool(agent_policy["approval_required"] or task_risk in {"high", "critical"}),
    }


def route_tasks(tasks: list[dict[str, Any]], registry: dict[str, Any] | None = None, policy: dict[str, Any] | None = None, model_catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    registry = registry or load_json(); policy = policy or load_policy(); model_catalog = model_catalog or load_catalog()
    order, groups = _topological_order(tasks)
    routes = [route_task(task, registry, policy, model_catalog) for task in tasks]
    for route, task in zip(routes, tasks):
        fallback_routes = []
        for fallback_agent in route["fallback_agents"]:
            try:
                fallback = route_task({**task, "preferred_agent": fallback_agent}, registry, policy, model_catalog)
            except RoutingError:
                continue
            if not set(fallback["authority"]) <= set(route["authority"]):
                raise RoutingError("fallback authority expansion is forbidden")
            fallback_routes.append(fallback)
        route["fallback_routes"] = fallback_routes
    return {"schema": "agent_routing_v1", "registry_revision": registry["revision"], "policy_revision": policy["revision"], "execution_order": order, "parallel_groups": groups, "routes": routes, "routing_digest": digest({"registry_revision": registry["revision"], "policy_revision": policy["revision"], "execution_order": order, "routes": routes})}
