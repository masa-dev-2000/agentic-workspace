"""Deterministic Skill entry selection for the local registry."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REGISTRY_PATH = Path(__file__).resolve().parents[2] / "skill-registry.yaml"
MAX_ROUTE_DEPTH = 8


class EntryRoutingError(ValueError):
    pass


@dataclass(frozen=True)
class EntryRoute:
    entrypoint: str
    skill: str | None
    reason: str
    depth: int


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    if not isinstance(loaded, dict) or not isinstance(loaded.get("skills"), list):
        raise EntryRoutingError("registry is unavailable or invalid")
    return loaded


def _skills(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {}
    for skill in registry["skills"]:
        if not isinstance(skill, dict) or not isinstance(skill.get("key"), str):
            raise EntryRoutingError("registry contains an invalid Skill")
        if skill["key"] in result:
            raise EntryRoutingError("registry contains duplicate Skill keys")
        result[skill["key"]] = skill
    return result


def validate_routes(registry: dict[str, Any]) -> None:
    skills = _skills(registry)
    for key, skill in skills.items():
        entrypoint = skill.get("entrypoint")
        if entrypoint not in {"hook", "project", "planning", "explicit", "adaptive", "human"}:
            raise EntryRoutingError(f"{key}: invalid entrypoint")
        for target in skill.get("delegatesTo", []):
            if target not in skills:
                raise EntryRoutingError(f"{key}: unknown delegate {target}")
            if target == key:
                raise EntryRoutingError(f"{key}: self-delegation is forbidden")

    graph = {key: list(skill.get("delegatesTo", [])) for key, skill in skills.items()}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(key: str, depth: int) -> None:
        if depth > MAX_ROUTE_DEPTH:
            raise EntryRoutingError("route depth exceeded")
        if key in visiting:
            raise EntryRoutingError("delegation cycle detected")
        if key in visited:
            return
        visiting.add(key)
        for child in graph[key]:
            visit(child, depth + 1)
        visiting.remove(key)
        visited.add(key)

    for key in graph:
        visit(key, 0)


def route_request(request: dict[str, Any], registry: dict[str, Any] | None = None) -> EntryRoute:
    registry = registry or load_registry()
    validate_routes(registry)
    skills = _skills(registry)
    explicit = request.get("explicit_skill")
    if explicit:
        skill = skills.get(explicit)
        if not skill:
            raise EntryRoutingError("explicit Skill is not registered")
        if skill.get("invocationPolicy") == "deprecated":
            return EntryRoute("explicit", explicit, "deprecated_compatibility_shim", 0)
        return EntryRoute("explicit", explicit, "explicit_request_wins", 0)
    if request.get("human_required") or request.get("approval_required"):
        return EntryRoute("human", "ai-project-manager:human-task-requester", "human_authority_required", 0)
    if request.get("project_id") or request.get("project_ledger"):
        return EntryRoute("project", "ai-project-manager:project-orchestrator", "project_state_present", 0)
    if request.get("plan_only"):
        return EntryRoute("planning", "planning", "plan_only_request", 0)
    if request.get("review_only"):
        return EntryRoute("explicit", "gan", "explicit_review_request", 0)
    if request.get("nontrivial", True):
        return EntryRoute("adaptive", "adaptive-orchestrator", "nontrivial_execution", 0)
    return EntryRoute("explicit", None, "trivial_direct_response", 0)
