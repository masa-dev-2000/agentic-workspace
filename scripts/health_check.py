#!/usr/bin/env python3
"""Active health probe for agentic-workspace.

Separate from validate_workspace.py (fast pure pre-push check). This script
actively exercises the live defenses and infrastructure instead of just
reading config, and writes a public health report outside the repo.

Checks (each strictly binary pass/fail):
1. DEFENSE PROBE — pipe synthetic hook stdin into every hooks/claude/*.sh hook
   and assert both directions (blocks the bad case, allows the good case).
2. WIRING — shells out to validate_workspace.py and bootstrap_workspace.py --check.
3. BACKUP FRESHNESS — newest dir under AGENTIC_BACKUP_ROOT must be <8 days old.
4. SCHEDULED TASKS — schtasks /Query for each kind=="scheduled-task" wiring entry.

Run: python -X utf8 scripts/health_check.py [--hooks-dir DIR]

Writes (outside the repo, this repo is public):
  %USERPROFILE%\\.claude\\health\\latest.md
  %USERPROFILE%\\.claude\\health\\history.jsonl   (appended)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import platform_adapter as pa

ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()

BACKUP_ROOT_DEFAULT = pa.default_backup_root()
BACKUP_MAX_AGE_DAYS = 8

HEALTH_DIR = pa.health_dir()
LATEST_MD = HEALTH_DIR / "latest.md"
HISTORY_JSONL = HEALTH_DIR / "history.jsonl"

# --- Probe registry ------------------------------------------------------
# Every hooks/claude/*.sh file must have a registered probe case (drift-coverage-completeness).
# Each probe case is (label, stdin_json, expected_exit, description).
HOOK_PROBES: dict[str, list[tuple[str, dict, int, str]]] = {
    "validate-command.sh": [
        (
            "block: rm -rf",
            {"tool_name": "Bash", "tool_input": {"command": "rm -rf /tmp/x"}},
            2,
            "destructive rm must be blocked",
        ),
        (
            "allow: git status",
            {"tool_name": "Bash", "tool_input": {"command": "git status"}},
            0,
            "benign command must be allowed",
        ),
    ],
    "protect-files.sh": [
        (
            "block: claude settings.json",
            {"tool_name": "Edit", "tool_input": {"file_path": "C:/Users/masa/.claude/settings.json"}},
            2,
            "protected config file must be blocked",
        ),
        (
            "allow: benign repo path",
            {"tool_name": "Edit", "tool_input": {"file_path": "C:/Users/masa/dev/ws-phase2/README.md"}},
            0,
            "ordinary repo file must be allowed",
        ),
    ],
    # audit-config.sh is a ConfigChange hook (logs, never blocks). It always
    # exits 0 by design; the probe checks it runs cleanly rather than "blocks".
    "audit-config.sh": [
        (
            "run: config change event",
            {"source": "test", "file_path": "C:/Users/masa/.claude/settings.json"},
            0,
            "ConfigChange hook must run and exit 0 (it only logs, never blocks)",
        ),
    ],
}


_BASH = pa.resolve_bash()


def run_hook(hook_path: Path, stdin_json: dict) -> tuple[int, str, str]:
    # bash on Windows (Git Bash) mangles backslash-separated Windows paths
    # passed as argv; use a forward-slash path instead.
    bash_path = str(hook_path).replace("\\", "/")
    # The hooks read `cat /dev/stdin`. MSYS's /dev/stdin does not reliably
    # resolve when stdin is an anonymous pipe created by Python's
    # subprocess (input=... over PIPE); it resolves fine when stdin is a
    # real file, so write the payload to a temp file and redirect from it.
    import tempfile

    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json", encoding="utf-8") as f:
        f.write(json.dumps(stdin_json))
        tmp_path = f.name
    try:
        with open(tmp_path, "r", encoding="utf-8") as stdin_f:
            proc = subprocess.run(
                [_BASH, bash_path],
                stdin=stdin_f,
                capture_output=True,
                text=True,
            )
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    return proc.returncode, proc.stdout, proc.stderr


def check_defense_probe(hooks_dir: Path) -> tuple[bool, list[str]]:
    lines: list[str] = []
    ok = True

    if not _BASH:
        return False, [
            "ERROR: no real Git Bash found (only the WSL stub or nothing on PATH) — "
            "the probe cannot execute hooks, so hook health is UNKNOWN, not OK"
        ]

    actual_hooks = sorted(p.name for p in hooks_dir.glob("*.sh"))
    if not actual_hooks:
        return False, [f"ERROR: no *.sh hooks found in {hooks_dir}"]

    registered = set(HOOK_PROBES)
    for name in actual_hooks:
        if name not in registered:
            ok = False
            lines.append(f"ERROR: {name}: no registered probe case (drift-coverage-completeness)")

    for name, cases in HOOK_PROBES.items():
        hook_path = hooks_dir / name
        if not hook_path.is_file():
            ok = False
            lines.append(f"FAIL: {name}: hook file not found at {hook_path}")
            continue
        for label, stdin_json, expected_exit, desc in cases:
            rc, out, err = run_hook(hook_path, stdin_json)
            if rc == expected_exit:
                lines.append(f"PASS: {name} [{label}]: exit {rc} as expected ({desc})")
            else:
                ok = False
                lines.append(
                    f"FAIL: {name} [{label}]: expected exit {expected_exit}, got {rc} "
                    f"({desc}) stderr={err.strip()!r}"
                )
    return ok, lines


def check_wiring() -> tuple[bool, list[str]]:
    lines: list[str] = []
    ok = True
    for script, args in (
        ("validate_workspace.py", []),
        ("bootstrap_workspace.py", ["--check"]),
    ):
        cmd = [sys.executable, "-X", "utf8", str(ROOT / "scripts" / script), *args]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            lines.append(f"PASS: {script} {' '.join(args)} exited 0")
        else:
            ok = False
            lines.append(f"FAIL: {script} {' '.join(args)} exited {proc.returncode}")
            for l in (proc.stdout + proc.stderr).splitlines():
                lines.append(f"    {l}")
    return ok, lines


def check_backup_freshness() -> tuple[bool, list[str]]:
    import os

    root = Path(os.environ.get("AGENTIC_BACKUP_ROOT", str(BACKUP_ROOT_DEFAULT)))
    if not root.is_dir():
        return False, [f"FAIL: backup root {root} does not exist — no backup has ever run"]

    subdirs = [p for p in root.iterdir() if p.is_dir()]
    if not subdirs:
        return False, [f"FAIL: backup root {root} exists but contains no backup directories"]

    newest = max(subdirs, key=lambda p: p.stat().st_mtime)
    age_days = (datetime.now(timezone.utc).timestamp() - newest.stat().st_mtime) / 86400
    if age_days > BACKUP_MAX_AGE_DAYS:
        return False, [
            f"FAIL: newest backup {newest.name} is {age_days:.1f} days old "
            f"(> {BACKUP_MAX_AGE_DAYS} day limit)"
        ]
    return True, [f"PASS: newest backup {newest.name} is {age_days:.1f} days old"]


def check_scheduled_tasks() -> tuple[bool | None, list[str]]:
    wiring_path = ROOT / "config" / "wiring.json"
    entries = json.loads(wiring_path.read_text(encoding="utf-8")).get("entries", [])
    tasks = [e for e in entries if e.get("kind") == "scheduled-task"]
    if not tasks:
        return True, ["PASS: no scheduled-task entries declared"]

    # Windows' "Last Run Time" format from schtasks; other platforms' query
    # mechanisms use their own timestamp shapes (or report "unverified"
    # outright, e.g. launchd has no last-run timestamp via `list`) and are
    # not accuracy-checked against interval here — only last_result is.
    WINDOWS_LAST_RUN_FORMAT = "%m/%d/%Y %I:%M:%S %p"

    lines: list[str] = []
    any_unverified = False
    ok = True
    for entry in tasks:
        name = entry["name"]
        interval = entry.get("interval_minutes")
        q = pa.query_task(name)

        if q["exists"] is None:
            any_unverified = True
            lines.append(f"UNVERIFIED: {name}: could not query task ({q.get('error', 'unknown')})")
            continue
        if q["exists"] is False:
            any_unverified = True
            lines.append(f"UNVERIFIED: {name}: task not found ({q.get('error', 'unknown')})")
            continue

        last_result = q["last_result"]
        last_run = q["last_run"]
        if last_result is None:
            any_unverified = True
            lines.append(f"UNVERIFIED: {name}: could not determine Last Result")
            continue

        entry_ok = True
        # Windows Task Scheduler success codes that are not literal 0:
        #   0x41301 (267009) SCHED_S_TASK_RUNNING     — running right now
        #   0x41303 (267011) SCHED_S_TASK_HAS_NOT_RUN — registered, first
        #                                               trigger still pending
        # Treating these as failures would make a healthy scheduler look broken.
        running = last_result in ("267009", "0x41301")
        never_ran = last_result in ("267011", "0x41303")
        if last_result not in ("0", "0x0") and not (never_ran or running):
            entry_ok = False
            lines.append(f"FAIL: {name}: Last Result = {last_result} (nonzero)")

        if never_ran:
            any_unverified = True
            lines.append(f"UNVERIFIED: {name}: registered but has not run yet (first trigger pending)")
            continue

        if last_run == "unverified":
            any_unverified = True
            lines.append(f"UNVERIFIED: {name}: Last Run Time not available from this platform's query mechanism")
        else:
            try:
                # The platform adapter normalizes every scheduler's timestamp to
                # ISO 8601 (locale-independent); fall back to the older Windows
                # locale format only if that is what we were handed.
                try:
                    last_run_dt = datetime.fromisoformat(last_run)
                except ValueError:
                    last_run_dt = datetime.strptime(last_run, WINDOWS_LAST_RUN_FORMAT)
                now = datetime.now(last_run_dt.tzinfo) if last_run_dt.tzinfo else datetime.now()
                age_minutes = (now - last_run_dt).total_seconds() / 60
                max_age = 2 * interval if interval else None
                if max_age is not None and age_minutes > max_age:
                    entry_ok = False
                    lines.append(
                        f"FAIL: {name}: last run {age_minutes:.0f} min ago "
                        f"(> 2x declared interval of {interval} min)"
                    )
            except ValueError:
                any_unverified = True
                lines.append(f"UNVERIFIED: {name}: could not parse Last Run Time '{last_run}'")
                continue

        if entry_ok:
            lines.append(f"PASS: {name}: Last Result={last_result}, Last Run Time={last_run}")
        else:
            ok = False

    if any_unverified and ok:
        return None, lines
    return ok, lines


def write_report(sections: list[tuple[str, bool | None, list[str]]]) -> str:
    HEALTH_DIR.mkdir(parents=True, exist_ok=True)

    fail_count = sum(1 for _, status, _ in sections if status is False)
    header = f"HEALTH FAIL ({fail_count})" if fail_count else "HEALTH OK"

    now = datetime.now(timezone.utc).isoformat()
    md_lines = [f"# Health Check — {now}", "", f"**{header}**", ""]
    for name, status, lines in sections:
        status_label = {True: "OK", False: "FAIL", None: "UNVERIFIED"}[status]
        md_lines.append(f"## {name}: {status_label}")
        md_lines.extend(f"- {l}" for l in lines)
        md_lines.append("")
    LATEST_MD.write_text("\n".join(md_lines), encoding="utf-8")

    history_entry = {
        "timestamp": now,
        "status": header,
        "sections": {
            name: {"status": status, "lines": lines} for name, status, lines in sections
        },
    }
    with HISTORY_JSONL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(history_entry, ensure_ascii=False) + "\n")

    return header


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--hooks-dir",
        default=str(ROOT / "hooks" / "claude"),
        help="Directory containing the hook scripts to probe (default: repo hooks/claude)",
    )
    args = parser.parse_args()
    hooks_dir = Path(args.hooks_dir)

    sections: list[tuple[str, bool | None, list[str]]] = []

    ok, lines = check_defense_probe(hooks_dir)
    sections.append(("DEFENSE PROBE", ok, lines))

    ok, lines = check_wiring()
    sections.append(("WIRING", ok, lines))

    ok, lines = check_backup_freshness()
    sections.append(("BACKUP FRESHNESS", ok, lines))

    ok, lines = check_scheduled_tasks()
    sections.append(("SCHEDULED TASKS", ok, lines))

    header = write_report(sections)

    print(header)
    for name, status, lines in sections:
        status_label = {True: "OK", False: "FAIL", None: "UNVERIFIED"}[status]
        print(f"[{status_label}] {name}")
        for l in lines:
            print(f"  {l}")

    # UNVERIFIED sections do not count as failures for exit code purposes,
    # but are reported distinctly from PASS.
    fail_count = sum(1 for _, status, _ in sections if status is False)
    return 1 if fail_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
