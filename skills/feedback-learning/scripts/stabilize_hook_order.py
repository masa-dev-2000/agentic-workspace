#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from configure_hook import (
    atomic_write,
    cutover_candidate,
    dispatcher_installed,
    installed,
    is_dispatcher_command,
    is_feedback_command,
    is_pm_direct_command,
    pm_direct_installed,
)


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def group_role(group: object) -> str:
    if not isinstance(group, dict):
        return "unrelated"
    roles: set[str] = set()
    for hook in group.get("hooks", []):
        if not isinstance(hook, dict):
            continue
        values = (hook.get("command"), hook.get("commandWindows"))
        if any(is_feedback_command(value) for value in values):
            roles.add("feedback")
        if any(is_pm_direct_command(value) for value in values):
            roles.add("pm-direct")
        if any(is_dispatcher_command(value) for value in values):
            roles.add("dispatcher")
    if len(roles) > 1:
        raise ValueError(f"mixed managed hook group: {sorted(roles)}")
    return next(iter(roles), "unrelated")


def stabilize(current: dict, reference: dict, skill_dir: Path) -> tuple[dict, dict]:
    """Restore unchanged hook slots from a pre-cutover reference without losing new hooks."""
    if not installed(current) or not pm_direct_installed(current):
        raise ValueError("current config must contain feedback and PM direct hooks")
    if dispatcher_installed(current):
        raise ValueError("current config must not contain the legacy dispatcher")

    reference_candidate, cutover = cutover_candidate(reference, skill_dir)
    if not cutover["ready"] or dispatcher_installed(reference_candidate):
        raise ValueError("reference cannot produce a complete direct-hook cutover")

    current_groups = current.get("hooks", {}).get("UserPromptSubmit", [])
    target_groups = reference_candidate.get("hooks", {}).get("UserPromptSubmit", [])
    used: set[int] = set()
    rebuilt: list = []

    for target in target_groups:
        role = group_role(target)
        match = None
        for index, candidate in enumerate(current_groups):
            if index in used:
                continue
            if role in {"feedback", "pm-direct"}:
                if group_role(candidate) == role:
                    match = index
                    break
            elif group_role(candidate) == "unrelated" and canonical(candidate) == canonical(target):
                match = index
                break
        if match is None:
            raise ValueError(f"current config no longer contains reference slot: {role}")
        used.add(match)
        rebuilt.append(copy.deepcopy(current_groups[match]))

    for index, group in enumerate(current_groups):
        if index not in used:
            rebuilt.append(copy.deepcopy(group))

    candidate = copy.deepcopy(current)
    candidate.setdefault("hooks", {})["UserPromptSubmit"] = rebuilt
    return candidate, {
        "ready": True,
        "changed": canonical(candidate) != canonical(current),
        "before_roles": [group_role(group) for group in current_groups],
        "after_roles": [group_role(group) for group in rebuilt],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Restore trust-slot-stable UserPromptSubmit ordering after direct cutover"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        current = json.loads(args.config.read_text(encoding="utf-8"))
        reference = json.loads(args.reference.read_text(encoding="utf-8"))
        candidate, plan = stabilize(
            current,
            reference,
            Path(__file__).resolve().parents[1],
        )
        if args.apply and plan["changed"]:
            atomic_write(args.config, candidate)
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "error_class": type(exc).__name__, "error": str(exc)}
            )
        )
        return 1
    print(
        json.dumps(
            {"ok": True, "applied": bool(args.apply and plan["changed"]), **plan},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
