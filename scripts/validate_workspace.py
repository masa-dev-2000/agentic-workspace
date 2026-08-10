#!/usr/bin/env python3
"""Mechanical validation for agentic-workspace.

Checks (exit 1 on any failure):
1. Agent frontmatter: required keys, name/filename match, description quality floors,
   tools/disallowedTools syntax.
2. Wiring: config/wiring.json entries (junction/symlink/copy/ledger/scheduled-task/
   unmanaged) validate against the schema in config/wiring.schema.md; junction/symlink
   live paths must resolve to their declared repo target; copy-synced dirs (derived
   from kind=="copy" entries) must match their live sources.
3. Criteria: schema of criteria/*.md and CRITERIA.md index consistency
   (use --fix to regenerate the index).
4. Leaked ledgers: no *.sqlite3*, *.db*, or *.key files anywhere in the repo
   (this repo is public).

Run: python -X utf8 scripts/validate_workspace.py [--fix] [--no-live]

--no-live skips the live-filesystem checks (drift + wiring liveness) that
require this machine's actual home directory (~/.claude, ~/.codex, etc.) to
exist. A CI runner has no such filesystem, so those checks would fail
spuriously there. --no-live still runs agents/skills/criteria/leak checks and
prints which check groups were skipped.
"""
from __future__ import annotations

import filecmp
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()

CRITERION_STATUSES = {"proposed", "active", "retired", "graduated"}
WIRING_KINDS = {"junction", "symlink", "copy", "ledger", "scheduled-task", "unmanaged"}

# Structured approval line format, see criteria/SCHEMA.md.
APPROVAL_LINE_RE = re.compile(
    r'Approval:\s*actor=\S+\s+channel=\S+\s+date=\d{4}-\d{2}-\d{2}\s+ref="[^"]*"'
)

errors: list[str] = []


def expand(p: str) -> Path:
    return Path(p.replace("~", str(HOME), 1)) if p.startswith("~") else (ROOT / p)


def load_wiring() -> list[dict]:
    path = ROOT / "config" / "wiring.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("entries", [])


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
    for entry in load_wiring():
        if entry.get("kind") != "copy":
            continue
        repo = expand(entry["repo"])
        live = expand(entry["live"])
        tag = entry["repo"]
        diff_tree(repo, live, tag, ignore=entry.get("exclude"))


def check_wiring(no_live: bool = False) -> None:
    """Validate config/wiring.json. Schema/structural checks always run; the
    live-filesystem checks (path existence, junction/symlink resolution) are
    skipped when no_live is True (see --no-live)."""
    entries = load_wiring()
    if not entries:
        err("config/wiring.json: no entries")
        return
    seen_ids: set[str] = set()
    for entry in entries:
        eid = entry.get("id")
        kind = entry.get("kind")
        tag = f"config/wiring.json:{eid or '?'}"
        if not eid:
            err("config/wiring.json: entry missing 'id'")
        elif eid in seen_ids:
            err(f"{tag}: duplicate id")
        else:
            seen_ids.add(eid)
        if kind not in WIRING_KINDS:
            err(f"{tag}: unknown kind '{kind}' (allowed: {sorted(WIRING_KINDS)})")
            continue
        if kind == "unmanaged":
            if not entry.get("reason"):
                err(f"{tag}: kind=unmanaged requires 'reason'")
            continue
        if kind == "ledger":
            if not entry.get("live"):
                err(f"{tag}: kind=ledger requires 'live'")
            if entry.get("repo"):
                err(f"{tag}: kind=ledger must not have 'repo'")
            if no_live:
                continue
            live = expand(entry["live"]) if entry.get("live") else None
            if live and not live.exists():
                err(f"{tag}: ledger live path missing: {live}")
            continue
        if kind == "scheduled-task":
            if not entry.get("name"):
                err(f"{tag}: kind=scheduled-task requires 'name'")
            if not entry.get("interval_minutes"):
                err(f"{tag}: kind=scheduled-task requires 'interval_minutes'")
            continue
        # junction, symlink, copy
        if not entry.get("live"):
            err(f"{tag}: kind={kind} requires 'live'")
            continue
        if kind == "copy":
            if not entry.get("repo"):
                err(f"{tag}: kind=copy requires 'repo'")
            continue  # existence/drift for copy entries is handled by check_drift()
        if kind == "junction":
            if not entry.get("repo"):
                err(f"{tag}: kind=junction requires 'repo'")
                continue
            target = expand(entry["repo"])
        else:  # symlink
            if entry.get("repo"):
                target = expand(entry["repo"])
            elif entry.get("target_live"):
                target = expand(entry["target_live"])
            else:
                err(f"{tag}: kind=symlink requires 'repo' or 'target_live'")
                continue
        if no_live:
            continue
        live = expand(entry["live"])
        if not live.exists():
            err(f"{tag}: live path missing: {live}")
            continue
        try:
            resolved = live.resolve()
            expected = target.resolve() if target.exists() else target
        except OSError as e:
            err(f"{tag}: could not resolve live path {live}: {e}")
            continue
        if resolved != expected:
            err(f"{tag}: live path {live} resolves to {resolved}, expected {expected}")


def check_no_ledgers_in_repo() -> None:
    for pattern in ("**/*.sqlite3*", "**/*.db*", "**/*.key"):
        for path in ROOT.glob(pattern):
            err(f"leaked ledger/secret file in public repo: {path.relative_to(ROOT)}")


def check_criteria(fix: bool) -> None:
    crit_dir = ROOT / "criteria"
    if not crit_dir.is_dir():
        return  # not bootstrapped yet — nothing to validate
    index_lines: list[str] = []
    for path in sorted(crit_dir.glob("*.md")):
        if path.name in ("CRITERIA.md", "SCHEMA.md"):
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
        if fm.get("status") == "active":
            body = path.read_text(encoding="utf-8")
            if not APPROVAL_LINE_RE.search(body):
                err(
                    f"{tag}: status=active requires a Status History line matching "
                    f"'Approval: actor=<provider>:<id> channel=<...> date=YYYY-MM-DD ref=\"...\"' "
                    f"(see criteria/SCHEMA.md)"
                )
    index_path = crit_dir / "CRITERIA.md"
    expected = "# Criteria Index\n\n" + "\n".join(index_lines) + "\n"
    if fix:
        index_path.write_text(expected, encoding="utf-8")
    elif not index_path.is_file() or index_path.read_text(encoding="utf-8") != expected:
        err("criteria/CRITERIA.md: index out of date (run with --fix to regenerate)")


def check_generated_docs(fix: bool) -> None:
    """Generated doc blocks must match their generators. A doc that only claims
    to be generated drifts exactly like a hand-written one."""
    sync = ROOT / "scripts" / "sync_generated_docs.py"
    if not sync.is_file():
        return
    args = [sys.executable, "-X", "utf8", str(sync)] + ([] if fix else ["--check"])
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=ROOT)
    if proc.returncode != 0:
        for line in (proc.stdout or proc.stderr).strip().splitlines():
            err(f"generated docs: {line.strip()}")


def main() -> int:
    fix = "--fix" in sys.argv
    no_live = "--no-live" in sys.argv
    check_agents()
    check_skills()
    check_wiring(no_live=no_live)
    if not no_live:
        check_drift()
    check_no_ledgers_in_repo()
    check_criteria(fix)
    check_generated_docs(fix)
    if no_live:
        print("skipped: drift check, wiring live-path/junction/symlink resolution (--no-live)")
    if errors:
        print(f"FAIL ({len(errors)} problems)")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("OK: agents valid, no drift, criteria consistent, wiring valid, no leaked ledgers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
