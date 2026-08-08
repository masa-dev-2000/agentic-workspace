from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


STATUSES = {
    "inbox",
    "candidate",
    "planned",
    "building",
    "implemented",
    "rejected",
    "superseded",
}
PRIORITIES = {"low", "normal", "high", "critical"}


def home() -> Path:
    override = os.environ.get("CODEX_SKILL_IDEA_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".codex" / "skill-idea-inbox").resolve()


def ledger_path() -> Path:
    root = home()
    root.mkdir(parents=True, exist_ok=True)
    return root / "ideas.jsonl"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_events() -> list[dict]:
    path = ledger_path()
    if not path.exists():
        return []
    events: list[dict] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid JSONL at line {line_number}: {exc}") from exc
        if not isinstance(event, dict) or event.get("schema_version") != 1:
            raise SystemExit(f"Unsupported event at line {line_number}")
        events.append(event)
    return events


def append_event(event: dict) -> None:
    path = ledger_path()
    payload = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def current_state(events: list[dict]) -> dict[str, dict]:
    state: dict[str, dict] = {}
    for event in events:
        idea_id = event["idea_id"]
        if event["action"] == "add":
            state[idea_id] = dict(event["fields"])
            state[idea_id]["idea_id"] = idea_id
            state[idea_id]["created_at"] = event["at"]
        elif event["action"] == "update" and idea_id in state:
            state[idea_id].update(event["fields"])
        if idea_id in state:
            state[idea_id]["updated_at"] = event["at"]
    return state


def emit(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def add(args: argparse.Namespace) -> int:
    events = read_events()
    state = current_state(events)
    normalized = args.title.strip().casefold()
    duplicates = [
        item for item in state.values()
        if item.get("title", "").strip().casefold() == normalized
        and item.get("status") not in {"rejected", "superseded"}
    ]
    if duplicates:
        emit({"added": False, "reason": "possible-duplicate", "matches": duplicates})
        return 2
    idea_id = f"idea-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"
    fields = {
        "title": args.title,
        "problem": args.problem,
        "desired_outcome": args.desired_outcome,
        "mvp": args.mvp,
        "later": args.later,
        "related_system": args.related_system,
        "priority": args.priority,
        "next_step": args.next_step,
        "tags": sorted(set(args.tags or [])),
        "status": args.status,
    }
    event = {
        "schema_version": 1,
        "event_id": f"event-{uuid.uuid4().hex}",
        "idea_id": idea_id,
        "action": "add",
        "at": now(),
        "fields": fields,
    }
    append_event(event)
    emit({"added": True, "idea": current_state([event])[idea_id]})
    return 0


def update(args: argparse.Namespace) -> int:
    events = read_events()
    state = current_state(events)
    if args.idea_id not in state:
        raise SystemExit(f"Unknown idea ID: {args.idea_id}")
    fields = {
        key: value
        for key, value in {
            "status": args.status,
            "priority": args.priority,
            "next_step": args.next_step,
            "mvp": args.mvp,
            "later": args.later,
        }.items()
        if value is not None
    }
    if not fields:
        raise SystemExit("No update fields supplied.")
    event = {
        "schema_version": 1,
        "event_id": f"event-{uuid.uuid4().hex}",
        "idea_id": args.idea_id,
        "action": "update",
        "at": now(),
        "fields": fields,
    }
    append_event(event)
    events.append(event)
    emit({"updated": True, "idea": current_state(events)[args.idea_id]})
    return 0


def list_ideas(args: argparse.Namespace) -> int:
    state = list(current_state(read_events()).values())
    if args.status:
        state = [item for item in state if item.get("status") == args.status]
    order = {"critical": 0, "high": 1, "normal": 2, "low": 3}
    state.sort(key=lambda item: (order.get(item.get("priority"), 9), item["created_at"]))
    emit({"count": len(state), "ideas": state})
    return 0


def show(args: argparse.Namespace) -> int:
    events = read_events()
    state = current_state(events)
    if args.idea_id not in state:
        raise SystemExit(f"Unknown idea ID: {args.idea_id}")
    history = [event for event in events if event["idea_id"] == args.idea_id]
    emit({"idea": state[args.idea_id], "history": history})
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Global Skill idea inbox")
    commands = root.add_subparsers(dest="command", required=True)

    add_cmd = commands.add_parser("add")
    add_cmd.add_argument("--title", required=True)
    add_cmd.add_argument("--problem", required=True)
    add_cmd.add_argument("--desired-outcome", required=True)
    add_cmd.add_argument("--mvp", required=True)
    add_cmd.add_argument("--later", default="")
    add_cmd.add_argument("--related-system", default="")
    add_cmd.add_argument("--priority", choices=sorted(PRIORITIES), default="normal")
    add_cmd.add_argument("--next-step", default="")
    add_cmd.add_argument("--tags", nargs="*", default=[])
    add_cmd.add_argument("--status", choices=sorted(STATUSES), default="inbox")
    add_cmd.set_defaults(func=add)

    update_cmd = commands.add_parser("update")
    update_cmd.add_argument("idea_id")
    update_cmd.add_argument("--status", choices=sorted(STATUSES))
    update_cmd.add_argument("--priority", choices=sorted(PRIORITIES))
    update_cmd.add_argument("--next-step")
    update_cmd.add_argument("--mvp")
    update_cmd.add_argument("--later")
    update_cmd.set_defaults(func=update)

    list_cmd = commands.add_parser("list")
    list_cmd.add_argument("--status", choices=sorted(STATUSES))
    list_cmd.set_defaults(func=list_ideas)

    show_cmd = commands.add_parser("show")
    show_cmd.add_argument("idea_id")
    show_cmd.set_defaults(func=show)
    return root


def main() -> int:
    args = parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
