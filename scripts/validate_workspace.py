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
4. Skill composition (skills/AGENTS.md:30-32, 66-72): SKILL.md body length,
   machine-specific absolute paths, inline schema blocks, command-block density.
   Pre-existing violations are acknowledged per skill and per rule in
   config/skill-composition-acknowledged.json, each naming its tracking issue.
5. Leaked ledgers: no *.sqlite3*, *.db*, or *.key files anywhere in the repo
   (this repo is public).

Run: python -X utf8 scripts/validate_workspace.py [--fix] [--no-live|--no-drift]

--no-live skips the live-filesystem checks (drift, wiring liveness, RULEBOOK
enforcement) that require this machine's actual home directory (~/.claude,
~/.codex, the RULEBOOK under ~/dev, etc.) to exist. A CI runner has no such
filesystem, so those checks would fail spuriously there.

--no-drift skips only drift + wiring liveness, i.e. the checks that compare the
repo against the ~/.codex, ~/.claude, ~/.agents junctions. Those junctions
point at the main checkout, so from a linked git worktree they compare
unrelated trees; RULEBOOK enforcement compares the RULEBOOK against *this*
tree and stays meaningful there, so a worktree must not lose it. Used by
.githooks/pre-push (issue #25).

Either flag still runs agents/skills/criteria/leak checks and prints which
check groups were skipped.
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
# Non-fatal findings. Per criterion validator-signal-hygiene the steady state
# here is empty: an accepted warning is recorded as an acknowledgement, not
# printed forever.
warnings: list[str] = []


def expand(p: str) -> Path:
    return Path(p.replace("~", str(HOME), 1)) if p.startswith("~") else (ROOT / p)


def load_wiring() -> list[dict]:
    path = ROOT / "config" / "wiring.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("entries", [])


def err(msg: str) -> None:
    errors.append(msg)


def warn(msg: str) -> None:
    warnings.append(msg)


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


### Skill/Runtime/Hook composition (skills/AGENTS.md:30-32, 66-72) ###########

# skills/AGENTS.md:30 states the limit verbatim ("Body under 500 lines").
# This is not a tuned number: it is the written rule, transcribed.
SKILL_BODY_MAX_LINES = 500

# skills/AGENTS.md:32 says "Store detailed schemas and contracts outside
# SKILL.md". A yaml/json fence needs a size that separates an illustrative
# snippet from a transcribed contract. Measured over all 47 SKILL.md files on
# 2026-08-11 with FENCE_RE below (indent- and tilde-tolerant, so the count is
# not a parser artifact): exactly one yaml/json fence exceeds 15 lines
# (adaptive-orchestrator's 38-line handoff schema, issue #13); every other one
# is <=15. 20 sits inside that gap, so the threshold accuses only blocks the
# corpus itself marks as outliers.
SKILL_INLINE_SCHEMA_MAX_LINES = 20

# skills/AGENTS.md:32 says "place deterministic behavior in scripts". Command
# fence count is a weak proxy for that (a count cannot tell a two-line usage
# example from embedded logic), so it is reported as a warning rather than an
# error — but an unacknowledged warning still fails the run, see main().
# Measured on 2026-08-11 with FENCE_RE below the counts are 18, 14, 11, 9, 3,
# 3, 2, ... — 10 sits in the gap between 9 and 11, the only natural break in
# the corpus.
SKILL_COMMAND_BLOCK_MAX = 10

SCHEMA_FENCE_LANGS = {"yaml", "yml", "json"}
COMMAND_FENCE_LANGS = {"bash", "sh", "shell", "powershell", "ps1", "cmd", "console"}
# Follows CommonMark closely enough that formatting choices are not escape
# hatches: either fence character, a run of 3+ (so a ````-wrapped example does
# not desync the pairing), and up to 3 spaces of indentation on both the opening
# and the closing fence (fences inside list items are routinely indented).
# The backreference makes the closer start with the opener's exact run, which is
# what well-formed CommonMark writes.
FENCE_RE = re.compile(
    r"^[ ]{0,3}(?P<fence>`{3,}|~{3,})(?P<lang>[A-Za-z0-9_+-]*)[^\n]*\r?\n"
    r"(?P<body>.*?)^[ ]{0,3}(?P=fence)",
    re.S | re.M,
)

# Machine-specific home directories in every form this workspace actually
# writes them: Windows drive paths, Git Bash /c/Users, and POSIX homes.
ABSOLUTE_HOME_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]Users[\\/]|(?:/[a-z])?/Users/|/home/)[A-Za-z0-9._-]"
)


def fenced_blocks(text: str) -> list[tuple[str, str]]:
    """(language, body) for every fenced block, lowercased language."""
    return [(m.group("lang").lower(), m.group("body")) for m in FENCE_RE.finditer(text)]

COMPOSITION_ACK_PATH = ROOT / "config" / "skill-composition-acknowledged.json"
COMPOSITION_RULES = ("body-length", "absolute-path", "inline-schema", "command-blocks")


def load_composition_acks() -> dict[str, dict[str, dict]]:
    """Per-skill, per-rule acknowledgements of the composition violations that
    predate this check. Blanket muting is not available on purpose: an entry
    names one skill, one rule, and the issue that tracks fixing it, so the
    exemption is visible and attributable. An entry disappears when the
    violation is fixed: check_skill_composition() fails on an entry whose skill
    no longer violates its rule (criterion validator-signal-hygiene).

    This file is hand-edited, so its shape is validated rather than assumed —
    a malformed entry must produce a finding, not a traceback that aborts every
    later check in the run."""
    if not COMPOSITION_ACK_PATH.is_file():
        return {}
    rel = COMPOSITION_ACK_PATH.relative_to(ROOT).as_posix()
    try:
        data = json.loads(COMPOSITION_ACK_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        err(f"{rel}: not valid JSON: {e}")
        return {}
    if not isinstance(data, dict):
        err(f"{rel}: top-level value must be an object with an 'acknowledged' key")
        return {}
    acks = data.get("acknowledged", {})
    if not isinstance(acks, dict):
        err(f"{rel}: 'acknowledged' must be an object of skill -> rule -> entry")
        return {}
    clean: dict[str, dict[str, dict]] = {}
    for skill, rules in acks.items():
        if not isinstance(rules, dict):
            err(f"{rel}: {skill}: value must be an object of rule -> entry")
            continue
        for rule, entry in rules.items():
            if rule not in COMPOSITION_RULES:
                err(f"{rel}: {skill}: unknown rule '{rule}' (allowed: {list(COMPOSITION_RULES)})")
                continue
            if not isinstance(entry, dict):
                err(f"{rel}: {skill}/{rule}: entry must be an object with 'issue' and 'reason'")
                continue
            if not re.fullmatch(r"#\d+", str(entry.get("issue", ""))):
                err(f"{rel}: {skill}/{rule}: 'issue' must name the tracking issue as '#<n>'")
            if not entry.get("reason"):
                err(f"{rel}: {skill}/{rule}: missing 'reason'")
            clean.setdefault(skill, {})[rule] = entry
    return clean


def check_skill_composition() -> None:
    """Enforce the Skill/Runtime/Hook composition rule (skills/AGENTS.md:66-72)
    on the SKILL.md body, which check_skills() never reads.

    Each finding is either a failure or an acknowledged, issue-tracked exemption.
    An acknowledgement for a skill that no longer violates the rule is itself a
    failure, so the baseline can only shrink."""
    skills_dir = ROOT / "skills"
    if not skills_dir.is_dir():
        return  # partial checkout; do not abort the rest of the run
    acks = load_composition_acks()
    rel_ack = COMPOSITION_ACK_PATH.relative_to(ROOT).as_posix()
    used: set[tuple[str, str]] = set()

    for d in sorted(skills_dir.iterdir()):
        sk = d / "SKILL.md"
        if not d.is_dir() or not sk.is_file():
            continue
        tag = f"skills/{d.name}"
        text = sk.read_text(encoding="utf-8")
        fences = fenced_blocks(text)
        findings: list[tuple[str, bool, str]] = []  # (rule, fatal, message)

        # "Body under 500 lines" means the body, so the frontmatter block is not
        # counted, and 500 itself is already not "under 500".
        body_text = re.sub(r"^---\r?\n.*?\r?\n---\r?\n", "", text, count=1, flags=re.S)
        n_lines = len(body_text.splitlines())
        if n_lines >= SKILL_BODY_MAX_LINES:
            findings.append(("body-length", True,
                             f"SKILL.md body is {n_lines} lines, not under the "
                             f"{SKILL_BODY_MAX_LINES}-line limit — move overflow to references/"))

        if ABSOLUTE_HOME_PATH_RE.search(text):
            hits = [str(i + 1) for i, line in enumerate(text.splitlines())
                    if ABSOLUTE_HOME_PATH_RE.search(line)]
            findings.append(("absolute-path", True,
                             f"SKILL.md hard-codes a machine-specific user path at line(s) "
                             f"{', '.join(hits)} — the skill will not load on another machine"))

        oversized = [len(body.splitlines()) for lang, body in fences
                     if lang in SCHEMA_FENCE_LANGS
                     and len(body.splitlines()) > SKILL_INLINE_SCHEMA_MAX_LINES]
        if oversized:
            findings.append(("inline-schema", True,
                             f"SKILL.md inlines {len(oversized)} {'/'.join(sorted(SCHEMA_FENCE_LANGS))} "
                             f"block(s) of {max(oversized)} lines (limit "
                             f"{SKILL_INLINE_SCHEMA_MAX_LINES}) — schemas and contracts belong "
                             f"outside SKILL.md"))

        n_cmd = sum(1 for lang, _ in fences if lang in COMMAND_FENCE_LANGS)
        if n_cmd > SKILL_COMMAND_BLOCK_MAX:
            findings.append(("command-blocks", False,
                             f"SKILL.md holds {n_cmd} command blocks (over "
                             f"{SKILL_COMMAND_BLOCK_MAX}) — deterministic behavior belongs in scripts/"))

        for rule, fatal, msg in findings:
            if rule in acks.get(d.name, {}):
                used.add((d.name, rule))
                continue
            (err if fatal else warn)(f"{tag}: {msg}")

    for skill, rules in acks.items():
        for rule in rules:
            if (skill, rule) not in used:
                err(f"{rel_ack}: {skill}/{rule} no longer violates the rule — "
                    f"delete the acknowledgement")


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
    """This repository is public. Guard by the NATURE of the content, not only by
    file extension — the first version of this check listed sqlite/db/key patterns
    and would have happily accepted another project's issue text, which is exactly
    the leak the markdown issue backend nearly caused."""
    for pattern in ("**/*.sqlite3*", "**/*.db*", "**/*.key"):
        for path in ROOT.glob(pattern):
            err(f"leaked ledger/secret file in public repo: {path.relative_to(ROOT)}")

    # Work items belong to the project they describe. An issues/ directory here
    # means some other project's issue text was routed into a public repo.
    issues_dir = ROOT / "issues"
    if issues_dir.exists():
        err("public repo must not hold an issues/ directory: work items belong in "
            "the project they describe (see agents/claude/issue-ledger.md)")


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


DOC_PATH_RE = re.compile(
    r"`((?:scripts|config|criteria|agents|hooks|commands|docs|skills|\.github|\.githooks)"
    r"/[A-Za-z0-9_./-]+)`"
)
DOC_FUNC_RE = re.compile(r"`([a-z_]{3,}\(\))`")


def check_doc_references() -> None:
    """Every repo path and function a doc names must exist.

    This is the part of doc accuracy a machine CAN judge. It does not check
    whether the prose is *right* — only that it does not point at things that
    are gone, which is how docs rot first and most visibly.
    """
    sources = "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in (ROOT / "scripts").glob("*.py")
    )
    for doc in sorted((ROOT / "docs").glob("*.md")) + [ROOT / "README.md", ROOT / "adapters" / "README.md"]:
        if not doc.is_file():
            continue
        text = doc.read_text(encoding="utf-8")
        rel = doc.relative_to(ROOT).as_posix()
        for path in sorted(set(DOC_PATH_RE.findall(text))):
            if not (ROOT / path).exists():
                err(f"{rel}: references missing path '{path}'")
        for func in sorted(set(DOC_FUNC_RE.findall(text))):
            if f"def {func[:-2]}(" not in sources:
                err(f"{rel}: references function '{func}' that no script defines")


RULEBOOK = HOME / "dev" / "00_work" / "00_ops-rulebook" / "RULEBOOK.md"
MAPPING_ROW_RE = re.compile(r"^\|\s*(R\d)\s*\|[^|]*\|\s*([a-z0-9-]+)\s*\|\s*([^|]+?)\s*\|\s*$", re.M)


def check_rulebook_enforcement() -> None:
    """A rule only fires if its trigger words are in the enforcing skill's
    description — description matching is the whole routing signal. On 2026-08-11
    R2 covered データ (data) while the skill's description listed only account /
    service / subscription, so a directory deletion never reached the skill.
    The rulebook's enforcement-mapping table is the source of truth; this reads it."""
    if not RULEBOOK.is_file():
        err(f"rulebook not found at {RULEBOOK} — enforcement mapping cannot be checked")
        return
    rows = MAPPING_ROW_RE.findall(RULEBOOK.read_text(encoding="utf-8"))
    if not rows:
        err("RULEBOOK.md: no enforcement-mapping rows found (expected a table of "
            "rule | scope | skill | required words)")
        return
    for rule, skill_name, words in rows:
        skill = ROOT / "skills" / skill_name / "SKILL.md"
        if not skill.is_file():
            err(f"RULEBOOK.md {rule}: enforcing skill '{skill_name}' does not exist")
            continue
        fm = parse_frontmatter(skill)
        desc = (fm or {}).get("description", "").lower()
        missing = [w.strip() for w in words.split(",")
                   if w.strip() and w.strip().lower() not in desc]
        if missing:
            err(f"RULEBOOK.md {rule}: skills/{skill_name} description is missing "
                f"trigger word(s) {missing} — the rule exists but will not fire")


def main() -> int:
    fix = "--fix" in sys.argv
    no_live = "--no-live" in sys.argv
    no_drift = "--no-drift" in sys.argv
    skip_junction_checks = no_live or no_drift
    check_agents()
    check_skills()
    check_skill_composition()
    check_wiring(no_live=skip_junction_checks)
    if not skip_junction_checks:
        check_drift()
    check_no_ledgers_in_repo()
    check_criteria(fix)
    check_generated_docs(fix)
    check_doc_references()
    if not no_live:
        check_rulebook_enforcement()
    if no_live:
        print("skipped: drift check, wiring live-path/junction/symlink resolution, "
              "RULEBOOK enforcement (--no-live)")
    elif no_drift:
        print("skipped: drift check, wiring live-path/junction/symlink resolution (--no-drift)")
    # A warning that never fails anything is the noise this workspace already
    # got burned by (issue #7, criterion validator-signal-hygiene): every
    # consumer of this script — .githooks/pre-push, .github/workflows/
    # validate.yml, scripts/health_check.py — gates on the exit code alone, so
    # an unacknowledged warning left green would print forever and be read by
    # nobody. The warn/error split is about the strength of the signal (a
    # command-block count is a proxy, a 500-line body is a fact), not about
    # whether it must be resolved. Acknowledging it is the way to make it stop.
    for w in warnings:
        print(f"WARN: {w}")
    if errors:
        print(f"FAIL ({len(errors)} problems)")
        for e in errors:
            print(f"  - {e}")
    if warnings:
        print(f"FAIL ({len(warnings)} unacknowledged warnings) — fix them, or record "
              f"each one in config/skill-composition-acknowledged.json with its tracking issue")
    if errors or warnings:
        return 1
    drift_claim = "drift not checked" if skip_junction_checks else "no drift"
    print(f"OK: agents valid, {drift_claim}, criteria consistent, wiring valid, "
          "no leaked ledgers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
