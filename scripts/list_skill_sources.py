#!/usr/bin/env python3
"""Show which skills each agent runtime actually loads, and from where.

Answers two questions that are otherwise invisible at runtime:
  1. Are Claude Code / Codex / the neutral path really reading THIS repo, or a
     stale copy? (compares resolved paths, not just names)
  2. Which of the skills offered in a session come from this workspace versus
     from the runtime itself or a plugin?

Run: python -X utf8 scripts/list_skill_sources.py [--names]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_SKILLS = ROOT / "skills"
HOME = Path.home()

RUNTIME_PATHS = [
    ("Claude Code", HOME / ".claude" / "skills"),
    ("Codex", HOME / ".codex" / "skills"),
    ("neutral (~/.agents)", HOME / ".agents" / "skills"),
]


def skills_in(path: Path) -> set[str]:
    if not path.is_dir():
        return set()
    return {d.name for d in path.iterdir() if d.is_dir() and (d / "SKILL.md").is_file()}


def main() -> int:
    show_names = "--names" in sys.argv
    workspace = skills_in(WORKSPACE_SKILLS)
    canonical = WORKSPACE_SKILLS.resolve()
    problems = 0

    print(f"WORKSPACE (canonical): {canonical}")
    print(f"  {len(workspace)} skills\n")

    print("RUNTIME WIRING")
    for label, path in RUNTIME_PATHS:
        if not path.exists():
            print(f"  [MISSING]  {label:20} {path}")
            problems += 1
            continue
        resolved = path.resolve()
        same = resolved == canonical
        found = skills_in(path)
        flag = "SHARED " if same else "SEPARATE"
        print(f"  [{flag}] {label:20} {path}")
        print(f"{'':13}-> {resolved}")
        print(f"{'':13}   {len(found)} skills"
              f"{'' if same else '  <-- NOT this repo'}")
        if not same:
            problems += 1
        extra, missing = found - workspace, workspace - found
        if extra or missing:
            problems += 1
            print(f"{'':13}   drift: only-here={sorted(extra)} missing={sorted(missing)}")

    # Other sources that also surface as "skills" in a Claude Code session.
    print("\nOTHER SKILL SOURCES IN A CLAUDE CODE SESSION (not this workspace)")
    cmds = sorted(p.stem for p in (HOME / ".claude" / "commands").glob("*.md"))
    print(f"  user slash commands ({len(cmds)}): {', '.join(cmds) if show_names else '--names to list'}")
    settings = HOME / ".claude" / "settings.json"
    if settings.is_file():
        plugins = list(json.loads(settings.read_text(encoding="utf-8")).get("enabledPlugins", {}))
        print(f"  enabled plugins ({len(plugins)}): {', '.join(plugins)}")
        print("     -> their skills appear namespaced, e.g. 'grok-build:check'")
    print("  runtime built-ins: anything else offered in-session (code-review, dataviz,")
    print("     artifact-*, init, security-review, …) ships with the CLI itself and is")
    print("     NOT in this repo. Rule of thumb: a bare name listed below is ours; a")
    print("     'plugin:name' is a plugin; everything else is built in.")

    if show_names:
        print(f"\nWORKSPACE SKILLS ({len(workspace)})")
        for name in sorted(workspace):
            print(f"  {name}")

    print(f"\n{'OK: all runtimes share this workspace' if problems == 0 else f'PROBLEMS: {problems}'}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
