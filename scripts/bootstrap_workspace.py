#!/usr/bin/env python3
"""Bootstrap/verify the machine wiring declared in config/wiring.json.

Modes:
  --check     (default) read-only. Per entry, print OK / MISSING / WRONG-TARGET
              plus the exact fix command. Exit non-zero if anything is not OK.
  --apply     create missing junctions, sync copy entries repo->live. Prints
              (never executes) schtasks /Create for scheduled-task entries.
  --markdown  emit the wiring table as markdown.

Run: python -X utf8 scripts/bootstrap_workspace.py [--check|--apply|--markdown]
"""
from __future__ import annotations

import filecmp
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import platform_adapter as pa

ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()


def expand(p: str) -> Path:
    return Path(p.replace("~", str(HOME), 1)) if p.startswith("~") else (ROOT / p)


def load_entries() -> list[dict]:
    data = json.loads((ROOT / "config" / "wiring.json").read_text(encoding="utf-8"))
    return data.get("entries", [])


def resolved_target(entry: dict) -> Path | None:
    if entry["kind"] == "junction":
        return expand(entry["repo"])
    if entry["kind"] == "symlink":
        if entry.get("repo"):
            return expand(entry["repo"])
        return expand(entry["target_live"])
    return None


def check_entry(entry: dict) -> tuple[str, str]:
    """Return (status, message) where status is OK / MISSING / WRONG-TARGET / SKIP."""
    kind = entry["kind"]
    eid = entry["id"]

    if kind == "unmanaged":
        return "SKIP", f"{eid}: unmanaged ({entry.get('reason', 'no reason given')})"

    if kind == "ledger":
        live = expand(entry["live"])
        if live.exists():
            return "OK", f"{eid}: ledger present at {live}"
        return "MISSING", f"{eid}: ledger missing at {live} (no fix command — ledgers are restored from backup, not repo)"

    if kind == "scheduled-task":
        cmd = pa.describe_register_command(entry["name"])
        return "OK", f"{eid}: scheduled-task (verify manually); recreate with: {cmd}"

    if kind == "copy":
        repo = expand(entry["repo"])
        live = expand(entry["live"])
        exclude = entry.get("exclude") or []
        if not live.exists():
            fix = f'cp -r "{repo}" "{live}"' if repo.is_dir() else f'cp "{repo}" "{live}"'
            return "MISSING", f"{eid}: live path missing: {live} -- fix: {fix}"
        if repo.is_file():
            same = filecmp.cmp(repo, live, shallow=False)
        else:
            cmp = filecmp.dircmp(str(repo), str(live), ignore=filecmp.DEFAULT_IGNORES + list(exclude))
            same = not (cmp.diff_files or cmp.left_only or cmp.right_only)
        if same:
            return "OK", f"{eid}: {live} matches {repo}"
        fix = f'cp -r "{repo}"/* "{live}"/' if repo.is_dir() else f'cp "{repo}" "{live}"'
        return "WRONG-TARGET", f"{eid}: drift between {repo} and {live} -- fix: {fix}"

    # junction / symlink
    live = expand(entry["live"])
    target = resolved_target(entry)
    if not live.exists():
        fix = pa.describe_link_command(live, target, kind)
        return "MISSING", f"{eid}: live path missing: {live} -- fix (run elevated if it fails): {fix}"
    try:
        resolved = pa.read_link_target(live)
        expected = target.resolve() if target.exists() else target
    except OSError as e:
        return "WRONG-TARGET", f"{eid}: could not resolve {live}: {e}"
    if resolved == expected:
        return "OK", f"{eid}: {live} -> {resolved}"
    fix = pa.describe_remove_and_relink_command(live, target, kind)
    return "WRONG-TARGET", f"{eid}: {live} resolves to {resolved}, expected {expected} -- fix (run elevated if it fails): {fix}"


def do_check() -> int:
    bad = 0
    for entry in load_entries():
        status, msg = check_entry(entry)
        print(f"{status}: {msg}")
        if status in ("MISSING", "WRONG-TARGET"):
            bad += 1
    return 1 if bad else 0


def do_apply() -> int:
    bad = 0
    for entry in load_entries():
        kind = entry["kind"]
        eid = entry["id"]
        status, _ = check_entry(entry)

        if kind == "scheduled-task":
            cmd = pa.describe_register_command(entry["name"])
            print(f"PRINT-ONLY: {eid}: would run: {cmd}")
            continue

        if kind in ("ledger", "unmanaged"):
            print(f"SKIP: {eid}: {kind} entries are not created by --apply")
            continue

        if status == "OK":
            print(f"OK: {eid}: already correct")
            continue

        if kind == "copy":
            repo = expand(entry["repo"])
            live = expand(entry["live"])
            try:
                if repo.is_dir():
                    exclude = set(entry.get("exclude") or [])
                    live.mkdir(parents=True, exist_ok=True)
                    for item in repo.iterdir():
                        if item.name in exclude:
                            continue
                        dest = live / item.name
                        if item.is_dir():
                            shutil.copytree(item, dest, dirs_exist_ok=True)
                        else:
                            shutil.copy2(item, dest)
                else:
                    live.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(repo, live)
                print(f"APPLIED: {eid}: synced {repo} -> {live}")
            except OSError as e:
                print(f"FAILED: {eid}: could not sync {repo} -> {live}: {e}")
                bad += 1
            continue

        if kind in ("junction", "symlink"):
            live = expand(entry["live"])
            target = resolved_target(entry)
            if live.exists():
                print(f"SKIP: {eid}: {live} exists but is WRONG-TARGET; remove it manually first, then rerun --apply")
                bad += 1
                continue
            ok, msg = pa.create_link(live, target, kind)
            if ok:
                print(f"APPLIED: {eid}: {msg}")
            else:
                fix = pa.describe_link_command(live, target, kind)
                print(
                    f"FAILED: {eid}: link creation failed: {msg}\n"
                    f"  Try running this shell elevated (Administrator on Windows), then rerun --apply, or run manually:\n"
                    f"  {fix}"
                )
                bad += 1
            continue

    # settings.claude.reference.json handling — never touch ~/.claude/settings.json directly
    ref = ROOT / "config" / "settings.claude.reference.json"
    live_settings = HOME / ".claude" / "settings.json"
    if ref.is_file():
        try:
            ref_data = json.loads(ref.read_text(encoding="utf-8"))
            live_data = json.loads(live_settings.read_text(encoding="utf-8")) if live_settings.is_file() else {}
            if ref_data != live_data:
                print("SETTINGS-DIFF: ~/.claude/settings.json differs from config/settings.claude.reference.json")
                print("Paste this fragment manually (this script will not write ~/.claude/settings.json):")
                print(json.dumps(ref_data, indent=2, ensure_ascii=False))
        except (OSError, json.JSONDecodeError) as e:
            print(f"SETTINGS-DIFF: could not compare settings: {e}")
    else:
        print("SETTINGS-DIFF: config/settings.claude.reference.json does not exist yet — nothing to diff against ~/.claude/settings.json")

    return 1 if bad else 0


def do_markdown() -> int:
    print("| id | kind | repo | live |")
    print("|---|---|---|---|")
    for entry in load_entries():
        repo = entry.get("repo", "")
        live = entry.get("live", entry.get("name", ""))
        print(f"| {entry['id']} | {entry['kind']} | {repo} | {live} |")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if "--apply" in args:
        return do_apply()
    if "--markdown" in args:
        return do_markdown()
    return do_check()


if __name__ == "__main__":
    raise SystemExit(main())
