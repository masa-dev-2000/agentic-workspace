from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence


DEFAULT_MANAGED_MARKERS = (
    "failure-learning\\scripts\\capture_hook.py",
    "failure-learning/scripts/capture_hook.py",
    "feedback-learning",
    "skill-telemetry",
    "ai-project-manager\\hooks\\user_prompt_dispatcher.py",
    "ai-project-manager/hooks/user_prompt_dispatcher.py",
)


def _canonical_hash(value: Mapping[str, Any]) -> str:
    body = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def build_cutover_plan(
    hooks_config: Mapping[str, Any],
    *,
    dispatcher_command: Sequence[str],
    managed_markers: Sequence[str] = DEFAULT_MANAGED_MARKERS,
) -> dict[str, Any]:
    if not dispatcher_command or not all(
        isinstance(part, str) and part for part in dispatcher_command
    ):
        raise ValueError("dispatcher_command must be a non-empty argv sequence")
    original = deepcopy(dict(hooks_config))
    hooks = original.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hooks_config.hooks must be an object")

    removals: list[dict[str, Any]] = []
    preserved: list[dict[str, Any]] = []
    affected_events: set[str] = set()
    normalized_markers = tuple(marker.lower() for marker in managed_markers)
    for event_name, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        for group_index, group in enumerate(groups):
            if not isinstance(group, dict):
                continue
            commands = group.get("hooks")
            if not isinstance(commands, list):
                continue
            for hook_index, hook in enumerate(commands):
                if not isinstance(hook, dict):
                    continue
                command = hook.get("commandWindows", hook.get("command", ""))
                command_text = command if isinstance(command, str) else ""
                command_hash = f"sha256:{hashlib.sha256(command_text.encode()).hexdigest()}"
                record = {
                    "event": event_name,
                    "groupIndex": group_index,
                    "hookIndex": hook_index,
                    "commandHash": command_hash,
                }
                if any(marker in command_text.lower() for marker in normalized_markers):
                    removals.append(record)
                    affected_events.add(event_name)
                else:
                    preserved.append(record)

    additions = [
        {
            "event": event,
            "matcher": "*",
            "commandArgv": list(dispatcher_command) + ["--event", event],
            "timeoutSeconds": 2,
        }
        for event in sorted(affected_events)
    ]
    return {
        "schemaVersion": "1.0",
        "mode": "proposal-only",
        "sourceHash": _canonical_hash(original),
        "scope": "global-collector-hooks",
        "readyToApply": False,
        "preconditions": [
            "content-vault-reference",
            "event-envelope-compatible-adapters",
            "single-batch-worker",
            "verified-cutover-test",
        ],
        "patchProposal": {
            "removeExact": removals,
            "add": additions,
            "preserveExact": preserved,
        },
        "legacyExternalCollectors": [
            {
                "id": "self-clone-plugin-hooks",
                "action": "preserve",
                "reason": (
                    "Plugin-managed Self Clone hooks remain external until an explicit "
                    "Plugin release and reinstall is approved."
                ),
            }
        ],
        "actions": {
            "writeHooksConfig": False,
            "modifyPlugin": False,
            "installPlugin": False,
            "activate": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print a hooks cutover proposal; never modify hooks.json or a Plugin."
    )
    parser.add_argument("--hooks", type=Path, required=True)
    parser.add_argument(
        "--dispatcher-arg",
        action="append",
        required=True,
        help="One dispatcher argv element. Repeat in argv order.",
    )
    args = parser.parse_args()
    try:
        config = json.loads(args.hooks.read_text(encoding="utf-8-sig"))
        if not isinstance(config, dict):
            raise ValueError("hooks root must be an object")
        plan = build_cutover_plan(config, dispatcher_command=args.dispatcher_arg)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "invalid", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
