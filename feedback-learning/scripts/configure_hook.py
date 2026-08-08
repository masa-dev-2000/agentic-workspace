from __future__ import annotations

import argparse
import copy
import json
import os
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from feedback_store import (
    PRIVACY_REPAIR_VERSION,
    SCHEMA_VERSION,
    FeedbackStore,
)


def default_config() -> Path:
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    return codex_home / "hooks.json"


def command_for(skill_dir: Path) -> str:
    hook = skill_dir / "scripts" / "capture_hook.py"
    return f'python -X utf8 "{hook}"'


def load_config(path: Path) -> dict:
    if not path.exists():
        return {"description": "Local evidence-learning lifecycle hooks.", "hooks": {}}
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict) or not isinstance(value.get("hooks", {}), dict):
        raise ValueError("hooks.json must contain an object with a hooks object")
    value.setdefault("hooks", {})
    return value


def is_feedback_command(value: object) -> bool:
    return (
        isinstance(value, str)
        and "feedback-learning" in value
        and "capture_hook.py" in value
    )


def is_dispatcher_command(value: object) -> bool:
    return isinstance(value, str) and "user_prompt_dispatcher.py" in value


def is_pm_direct_command(value: object) -> bool:
    return (
        isinstance(value, str)
        and "capture_prompt.py" in value
        and "user_prompt_dispatcher.py" not in value
    )


def entries(config: dict) -> list:
    return config.setdefault("hooks", {}).setdefault("UserPromptSubmit", [])


def _hook_matches(hook: object, predicate) -> bool:
    return (
        isinstance(hook, dict)
        and (
            predicate(hook.get("command"))
            or predicate(hook.get("commandWindows"))
        )
    )


def _count(config: dict, predicate) -> int:
    return sum(
        1
        for group in entries(config)
        if isinstance(group, dict)
        for hook in group.get("hooks", [])
        if _hook_matches(hook, predicate)
    )


def installed(config: dict) -> bool:
    return _count(config, is_feedback_command) > 0


def dispatcher_installed(config: dict) -> bool:
    return _count(config, is_dispatcher_command) > 0


def pm_direct_installed(config: dict) -> bool:
    return _count(config, is_pm_direct_command) > 0


def install(config: dict, skill_dir: Path) -> bool:
    if installed(config):
        return False
    command = command_for(skill_dir.resolve())
    entries(config).append({
        "hooks": [{
            "type": "command",
            "command": command,
            "commandWindows": command,
            "timeout": 2,
        }]
    })
    config["description"] = "Collect local privacy-safe lifecycle evidence."
    return True


def uninstall(config: dict) -> bool:
    original = entries(config)
    kept = []
    changed = False
    for group in original:
        if not isinstance(group, dict):
            kept.append(group)
            continue
        hooks = [
            hook
            for hook in group.get("hooks", [])
            if not _hook_matches(hook, is_feedback_command)
        ]
        if len(hooks) != len(group.get("hooks", [])):
            changed = True
        if hooks:
            updated = dict(group)
            updated["hooks"] = hooks
            kept.append(updated)
    config["hooks"]["UserPromptSubmit"] = kept
    if not kept:
        config["hooks"].pop("UserPromptSubmit", None)
    return changed


def _pm_direct_group_from_dispatcher(config: dict) -> dict | None:
    for group in entries(config):
        if not isinstance(group, dict):
            continue
        for hook in group.get("hooks", []):
            if not _hook_matches(hook, is_dispatcher_command):
                continue
            direct = dict(hook)
            replaced = False
            for key in ("command", "commandWindows"):
                value = direct.get(key)
                if is_dispatcher_command(value):
                    direct[key] = str(value).replace(
                        "user_prompt_dispatcher.py",
                        "capture_prompt.py",
                    )
                    replaced = True
            if not replaced:
                continue
            result = {key: copy.deepcopy(value) for key, value in group.items() if key != "hooks"}
            result["hooks"] = [direct]
            return result
    return None


def _replace_dispatcher_with_pm_direct(config: dict) -> bool:
    """Replace a dispatcher hook in place so unrelated hook trust slots stay stable."""
    groups = entries(config)
    changed = False
    updated_groups = []
    for group in groups:
        if not isinstance(group, dict):
            updated_groups.append(group)
            continue
        updated_hooks = []
        group_changed = False
        for hook in group.get("hooks", []):
            if not _hook_matches(hook, is_dispatcher_command):
                updated_hooks.append(hook)
                continue
            direct = dict(hook)
            replaced = False
            for key in ("command", "commandWindows"):
                value = direct.get(key)
                if is_dispatcher_command(value):
                    direct[key] = str(value).replace(
                        "user_prompt_dispatcher.py",
                        "capture_prompt.py",
                    )
                    replaced = True
            updated_hooks.append(direct if replaced else hook)
            group_changed = group_changed or replaced
            changed = changed or replaced
        if group_changed:
            updated = dict(group)
            updated["hooks"] = updated_hooks
            updated_groups.append(updated)
        else:
            updated_groups.append(group)
    config["hooks"]["UserPromptSubmit"] = updated_groups
    return changed


def _remove_dispatcher(config: dict) -> bool:
    kept_groups = []
    changed = False
    for group in entries(config):
        if not isinstance(group, dict):
            kept_groups.append(group)
            continue
        hooks = [
            hook
            for hook in group.get("hooks", [])
            if not _hook_matches(hook, is_dispatcher_command)
        ]
        if len(hooks) != len(group.get("hooks", [])):
            changed = True
        if hooks:
            updated = dict(group)
            updated["hooks"] = hooks
            kept_groups.append(updated)
    config["hooks"]["UserPromptSubmit"] = kept_groups
    if not kept_groups:
        config["hooks"].pop("UserPromptSubmit", None)
    return changed


def cutover_candidate(config: dict, skill_dir: Path) -> tuple[dict, dict]:
    """Build an atomic direct-hook cutover without mutating the input."""
    candidate = copy.deepcopy(config)
    before = {
        "feedback_direct": installed(candidate),
        "pm_direct": pm_direct_installed(candidate),
        "dispatcher": dispatcher_installed(candidate),
    }
    added_pm_direct = False
    if not pm_direct_installed(candidate):
        added_pm_direct = _replace_dispatcher_with_pm_direct(candidate)
    install(candidate, skill_dir)
    ready = installed(candidate) and pm_direct_installed(candidate)
    if ready:
        _remove_dispatcher(candidate)
    after = {
        "feedback_direct": installed(candidate),
        "pm_direct": pm_direct_installed(candidate),
        "dispatcher": dispatcher_installed(candidate),
    }
    removed_dispatcher = before["dispatcher"] and not after["dispatcher"]
    return candidate, {
        "before": before,
        "after": after,
        "ready": ready,
        "blocked_reason": None if ready else "direct-pm-collector-unavailable",
        "added_feedback_direct": not before["feedback_direct"] and after["feedback_direct"],
        "added_pm_direct": added_pm_direct,
        "removed_dispatcher": removed_dispatcher,
        "preserved_direct_pm": after["pm_direct"],
    }


def atomic_write(path: Path, config: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    fd, temp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def database_needs_migration(db_path: Path) -> bool:
    if not db_path.is_file():
        return False
    try:
        source_uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
        db = sqlite3.connect(source_uri, uri=True, timeout=2)
        try:
            db.execute("PRAGMA query_only=ON")
            rows = dict(
                db.execute(
                    """SELECT key,value FROM meta
                       WHERE key IN ('schema_version','privacy_repair_version')"""
                ).fetchall()
            )
        finally:
            db.close()
        return not (
            rows.get("schema_version") == str(SCHEMA_VERSION)
            and rows.get("privacy_repair_version")
            == PRIVACY_REPAIR_VERSION
        )
    except sqlite3.Error:
        # Unknown or legacy layouts must be preserved before initialization
        # attempts a migration.
        return True


def backup_database(db_path: Path) -> Path:
    """Create a consistent, privacy-repaired current backup before migration.

    The online copy includes committed WAL data. The copy itself is migrated in
    an isolated temporary root first, so the retained backup never preserves the
    legacy Hook prompt-like templates that v3 is designed to remove.
    """
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final = backup_dir / (
        f"feedback-pre-live-migration-v{SCHEMA_VERSION}-"
        f"{stamp}-{uuid.uuid4().hex[:8]}.sqlite3"
    )
    with tempfile.TemporaryDirectory(
        prefix=".feedback-backup-",
        dir=backup_dir,
    ) as temp_name:
        isolated_root = Path(temp_name)
        isolated_db = isolated_root / "feedback.sqlite3"
        source_uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
        source = sqlite3.connect(source_uri, uri=True, timeout=5)
        destination = sqlite3.connect(isolated_db)
        try:
            source.backup(destination)
            destination.commit()
        finally:
            destination.close()
            source.close()

        # Apply the same secure-delete/WAL-finalized migration to the retained
        # backup before touching the live database.
        FeedbackStore(root=isolated_root).initialize()
        verified = sqlite3.connect(isolated_db)
        try:
            integrity = verified.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise sqlite3.DatabaseError("backup-integrity-check-failed")
        finally:
            verified.close()
        os.replace(isolated_db, final)
    return final


def provision_state(config_path: Path) -> dict:
    # Tests and isolated installations may point CODEX_FEEDBACK_LEARNING_HOME
    # elsewhere. Otherwise colocate state with the selected Codex config.
    root = (
        Path(os.environ["CODEX_FEEDBACK_LEARNING_HOME"]).expanduser().resolve()
        if os.environ.get("CODEX_FEEDBACK_LEARNING_HOME")
        else (config_path.resolve().parent / "feedback-learning")
    )
    store = FeedbackStore(root=root)
    backup = (
        backup_database(store.db_path)
        if database_needs_migration(store.db_path)
        else None
    )
    store.initialize()
    drained = store.drain_spool()
    return {
        "root": str(root),
        "key_exists": store.key_path.is_file(),
        "database_exists": store.db_path.is_file(),
        "migration_backup": str(backup) if backup else None,
        "drain": drained,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Manage the feedback-learning UserPromptSubmit hook"
    )
    parser.add_argument("action", choices=("status", "install", "uninstall", "cutover"))
    parser.add_argument("--config", type=Path, default=default_config())
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply an otherwise dry-run cutover atomically.",
    )
    args = parser.parse_args(argv)
    config = load_config(args.config)
    before = installed(config)
    changed = False
    provisioned = None
    plan = None

    if args.action == "install":
        provisioned = provision_state(args.config)
        changed = install(config, Path(__file__).resolve().parents[1])
    elif args.action == "uninstall":
        changed = uninstall(config)
    elif args.action == "cutover":
        candidate, plan = cutover_candidate(
            config,
            Path(__file__).resolve().parents[1],
        )
        if args.apply:
            if not plan["ready"]:
                raise ValueError(plan["blocked_reason"])
            provisioned = provision_state(args.config)
            config = candidate
            changed = config != load_config(args.config)

    if changed and (
        args.action != "cutover"
        or (args.action == "cutover" and args.apply)
    ):
        atomic_write(args.config, config)

    result = {
        "config": str(args.config.resolve()),
        "installed": installed(config),
        "pm_direct_installed": pm_direct_installed(config),
        "dispatcher_installed": dispatcher_installed(config),
        "changed": changed,
        "previously_installed": before,
        "dry_run": args.action == "cutover" and not args.apply,
        "provisioned": provisioned,
        "cutover": plan,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
