#!/usr/bin/env python3
"""Mechanical validation for agentic-workspace.

Checks (exit 1 on any failure):
1. Agent frontmatter: required keys, name/filename match, description quality floors,
   tools/disallowedTools syntax.
2. Drift: copy-synced dirs (hooks/, commands/, config/) must match their live sources.
3. Criteria: schema of criteria/*.md and CRITERIA.md index consistency
   (use --fix to regenerate the index).

Run: python -X utf8 scripts/validate_workspace.py [--fix]
"""
from __future__ import annotations

import filecmp
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()

CRITERION_STATUSES = {"proposed", "active", "retired", "graduated"}

# repo path -> live path (copy-synced; junctioned dirs need no check)
DRIFT_MAP = {
    ROOT / "hooks" / "claude": HOME / ".claude" / "hooks",
    ROOT / "hooks" / "codex" / "hooks.json": HOME / ".codex" / "hooks.json",
    ROOT / "commands" / "claude": HOME / ".claude" / "commands",
    ROOT / "config" / "CLAUDE.global.md": HOME / ".claude" / "CLAUDE.md",
    ROOT / "config" / "CLAUDE.user-root.md": HOME / "CLAUDE.md",
}
# hooks/codex/ scripts sync to ~/.codex/hooks/, but hooks/codex/hooks.json syncs
# to ~/.codex/hooks.json (covered by DRIFT_MAP above) — so it is excluded from the
# directory comparison. Keep this list explicit; do not add silent exclusions.
CODEX_HOOK_EXCLUDE = ["hooks.json"]

errors: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def parse_frontmatter(path: Path) -> dict[str, str] | None:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    if not m:
        return None
    fields: dict[str, str] = {}
    key = None
    for line in m.group(1).splitlines():
        km = re.match(r"^([A-Za-z][A-Za-z-]*):\s*(.*)$", line)
        if km:
            key = km.group(1)
            fields[key] = km.group(2).strip()
        elif key and line.startswith(" "):
            fields[key] += " " + line.strip()
    return fields


def check_agents() -> None:
    live = ROOT / "agents" / "claude"
    proposed = ROOT / "agents" / "proposed"
    paths = sorted(live.glob("*.md")) + sorted(proposed.glob("*.md")) if proposed.is_dir() else sorted(live.glob("*.md"))
    live_names = {p.stem for p in live.glob("*.md")}
    for path in paths:
        if path.parent == proposed and path.stem in live_names:
            err(f"agents/proposed/{path.name}: same name as a live agent — remove one")
        fm = parse_frontmatter(path)
        tag = f"agents/{path.parent.name}/{path.name}"
        if fm is None:
            err(f"{tag}: missing or malformed frontmatter block")
            continue
        for req in ("name", "description"):
            if not fm.get(req):
                err(f"{tag}: missing required frontmatter key '{req}'")
        if fm.get("name") and fm["name"] != path.stem:
            err(f"{tag}: name '{fm['name']}' does not match filename '{path.stem}'")
        desc = fm.get("description", "")
        if desc and len(desc) < 60:
            err(f"{tag}: description under 60 chars — too thin to route on")
        if desc and re.match(r"(?i)^(you|i)\b", desc):
            err(f"{tag}: description must be third person (starts with '{desc.split()[0]}')")
        for key in ("tools", "disallowedTools"):
            if key in fm and not re.fullmatch(r"[A-Za-z_*][\w*-]*(?:\s*,\s*[A-Za-z_*][\w*-]*)*", fm[key]):
                err(f"{tag}: {key} is not a comma-separated tool list: '{fm[key]}'")


PORTABLE_SKILL_FIELDS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}


def check_skills() -> None:
    """Enforce the Agent Skills spec portable core (agentskills.io) on every skill,
    so the tree stays loadable by any conforming agent service."""
    for d in sorted((ROOT / "skills").iterdir()):
        sk = d / "SKILL.md"
        if not d.is_dir() or not sk.is_file():
            continue
        tag = f"skills/{d.name}"
        fm = parse_frontmatter(sk)
        if fm is None:
            err(f"{tag}: SKILL.md missing frontmatter")
            continue
        name = fm.get("name", "")
        desc = fm.get("description", "")
        if name != d.name:
            err(f"{tag}: name '{name}' != directory name")
        if not re.fullmatch(r"[a-z0-9-]{1,64}", name or ""):
            err(f"{tag}: name violates spec charset/length")
        if not desc:
            err(f"{tag}: missing description")
        elif len(desc) > 1024:
            err(f"{tag}: description {len(desc)} chars exceeds spec max 1024")
        nonportable = set(fm) - PORTABLE_SKILL_FIELDS
        if nonportable:
            err(f"{tag}: vendor-specific frontmatter {sorted(nonportable)} — move to adapters or the metadata map")


def diff_tree(repo: Path, live: Path, tag: str, ignore: list[str] | None = None) -> None:
    if not live.exists():
        err(f"{tag}: live path missing: {live}")
        return
    if repo.is_file():
        if not filecmp.cmp(repo, live, shallow=False):
            err(f"{tag}: drift between {repo} and {live}")
        return
    cmp = filecmp.dircmp(str(repo), str(live), ignore=filecmp.DEFAULT_IGNORES + list(ignore or []))
    for name in cmp.diff_files:
        err(f"{tag}: drift in {name}")
    for name in cmp.left_only:
        err(f"{tag}: only in repo: {name}")
    for name in cmp.right_only:
        err(f"{tag}: only in live dir (not synced to repo): {name}")


def check_drift() -> None:
    for repo, live in DRIFT_MAP.items():
        diff_tree(repo, live, str(repo.relative_to(ROOT)))
    diff_tree(ROOT / "hooks" / "codex", HOME / ".codex" / "hooks",
              "hooks/codex", ignore=CODEX_HOOK_EXCLUDE)


def check_criteria(fix: bool) -> None:
    crit_dir = ROOT / "criteria"
    if not crit_dir.is_dir():
        return  # not bootstrapped yet — nothing to validate
    index_lines: list[str] = []
    for path in sorted(crit_dir.glob("*.md")):
        if path.name == "CRITERIA.md":
            continue
        fm = parse_frontmatter(path)
        tag = f"criteria/{path.name}"
        if fm is None:
            err(f"{tag}: missing frontmatter")
            continue
        for req in ("id", "statement", "status"):
            if not fm.get(req):
                err(f"{tag}: missing '{req}'")
        if fm.get("status") and fm["status"] not in CRITERION_STATUSES:
            err(f"{tag}: unknown status '{fm['status']}' (allowed: {sorted(CRITERION_STATUSES)})")
        if fm.get("id") and fm["id"] != path.stem:
            err(f"{tag}: id '{fm['id']}' does not match filename")
        if fm.get("id") and fm.get("statement"):
            index_lines.append(f"- `{fm['id']}` [{fm.get('status', '?')}] {fm['statement']}")
    index_path = crit_dir / "CRITERIA.md"
    expected = "# Criteria Index\n\n" + "\n".join(index_lines) + "\n"
    if fix:
        index_path.write_text(expected, encoding="utf-8")
    elif not index_path.is_file() or index_path.read_text(encoding="utf-8") != expected:
        err("criteria/CRITERIA.md: index out of date (run with --fix to regenerate)")


def main() -> int:
    fix = "--fix" in sys.argv
    check_agents()
    check_skills()
    check_drift()
    check_criteria(fix)
    if errors:
        print(f"FAIL ({len(errors)} problems)")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("OK: agents valid, no drift, criteria consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
