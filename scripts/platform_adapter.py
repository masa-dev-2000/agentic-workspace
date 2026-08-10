#!/usr/bin/env python3
"""OS platform adapter — the ONLY module in this repo allowed to branch on
sys.platform. Every script that has an OS-specific difference (link
creation/removal, scheduler registration/query, shell resolution, default
paths) calls into this module instead of embedding its own `if sys.platform`
check. See docs/OPERATIONS.md ("Other platforms") and config/wiring.schema.md
("kind semantics per platform") for how these differences map onto wiring
entries.

Platforms: win32 (Windows), darwin (macOS), linux (Linux — systemd --user
preferred; if unavailable, a documented crontab line is returned instead of
silently doing nothing).

Run standalone to unit-test the pure/monkeypatchable parts against all three
platforms without needing to actually run on them:
    python -X utf8 scripts/platform_adapter.py --self-test
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

WINDOWS = "win32"
MACOS = "darwin"
LINUX = "linux"


def _plat() -> str:
    return sys.platform


# --------------------------------------------------------------------------
# 1. LINKS
# --------------------------------------------------------------------------

def create_link(live: Path, target: Path, kind: str) -> tuple[bool, str]:
    """Create the live -> target link. Returns (ok, message). Callers are
    expected to have already checked `live` doesn't exist (bootstrap_workspace
    treats an existing-but-wrong link as WRONG-TARGET, not something to
    silently overwrite)."""
    if _plat() == WINDOWS:
        flag = "/J" if kind == "junction" else "/D"
        result = subprocess.run(
            ["cmd", "/c", "mklink", flag, str(live), str(target)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return True, f"created {kind} {live} -> {target}"
        return False, result.stderr.strip() or result.stdout.strip()

    # macOS/Linux: `junction` and `symlink` wiring kinds both collapse to a
    # plain symlink — there is no junction concept on POSIX. Documented in
    # config/wiring.schema.md rather than inventing a new wiring kind.
    try:
        live.parent.mkdir(parents=True, exist_ok=True)
        live.symlink_to(target, target_is_directory=target.is_dir())
        return True, f"created symlink {live} -> {target}"
    except OSError as e:
        return False, str(e)


def describe_link_command(live: Path, target: Path, kind: str) -> str:
    """Human-runnable fix string for creating this link (never executed)."""
    if _plat() == WINDOWS:
        flag = "/J" if kind == "junction" else "/D"
        return f'cmd /c mklink {flag} "{live}" "{target}"'
    return f'ln -s "{target}" "{live}"'


def describe_remove_and_relink_command(live: Path, target: Path, kind: str) -> str:
    """Human-runnable fix string for retargeting an existing wrong link."""
    if _plat() == WINDOWS:
        remove = "rmdir" if kind == "junction" else "del"
        return f'cmd /c {remove} "{live}" && ' + describe_link_command(live, target, kind)
    return f'rm "{live}" && ' + describe_link_command(live, target, kind)


def read_link_target(path: Path) -> Path:
    """Resolve what a link currently points at.

    Windows: junctions and dir-symlinks are both reparse points that
    Path.resolve() follows correctly via the Windows API, so it is used
    directly (unchanged from the pre-adapter behavior).

    POSIX: os.readlink() is used instead of .resolve(), so a relative
    symlink target is reported relative to the link's own directory rather
    than silently absolutized in a way that could mask a retarget.
    """
    if _plat() == WINDOWS:
        return path.resolve()
    target = Path(os.readlink(path))
    if not target.is_absolute():
        target = (path.parent / target).resolve()
    return target


def remove_link(path: Path) -> None:
    """Remove a link (junction/dir-symlink on Windows, symlink on POSIX)."""
    if _plat() == WINDOWS:
        subprocess.run(["cmd", "/c", "rmdir", str(path)], capture_output=True, text=True, check=True)
    else:
        path.unlink()


# --------------------------------------------------------------------------
# 2. SCHEDULER
# --------------------------------------------------------------------------

def _systemd_user_available() -> bool:
    return shutil.which("systemctl") is not None


def _launchagents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def _systemd_user_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def _launchd_label(name: str) -> str:
    return f"com.agentic-workspace.{name}"


def _launchd_plist_path(name: str) -> Path:
    return _launchagents_dir() / f"{_launchd_label(name)}.plist"


def _launchd_plist(name: str, command: list[str], interval_minutes: int) -> str:
    """Pure content generator (no I/O) so it can be unit-tested directly."""
    label = _launchd_label(name)
    arg_xml = "".join(f"    <string>{a}</string>\n" for a in command)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n<dict>\n'
        f"  <key>Label</key><string>{label}</string>\n"
        "  <key>ProgramArguments</key>\n  <array>\n"
        f"{arg_xml}  </array>\n"
        f"  <key>StartInterval</key><integer>{interval_minutes * 60}</integer>\n"
        "  <key>RunAtLoad</key><false/>\n"
        "</dict>\n</plist>\n"
    )


def _systemd_service_unit(name: str, command: list[str], schedule_hint: str, interval_minutes: int) -> str:
    """Pure content generator (no I/O)."""
    exec_start = " ".join(command)
    return (
        f"[Unit]\nDescription={name} ({schedule_hint or f'every {interval_minutes} min'})\n\n"
        f"[Service]\nType=oneshot\nExecStart={exec_start}\n"
    )


def _systemd_timer_unit(name: str, interval_minutes: int) -> str:
    """Pure content generator (no I/O)."""
    return (
        f"[Unit]\nDescription={name} timer\n\n"
        f"[Timer]\nOnUnitActiveSec={interval_minutes}min\nOnBootSec={interval_minutes}min\nPersistent=true\n\n"
        "[Install]\nWantedBy=timers.target\n"
    )


def register_task(name: str, command: list[str], interval_minutes: int, schedule_hint: str = "") -> tuple[bool, str]:
    """Register a recurring task. `command` is an argv list, e.g.
    [sys.executable, "-X", "utf8", "/abs/path/script.py"]. `schedule_hint` is
    an optional human note (e.g. "weekly Monday 09:00") used only in
    generated unit comments, never parsed.

    TASK DEFINITIONS (name, interval_minutes, description) stay single-source
    in config/wiring.json — this function only generates the platform unit
    from what the caller passes in; it does not itself read wiring.json.

    Windows: NOT implemented here. scripts/register_tasks.py owns Windows
    registration (schtasks /Create /XML against the existing
    scripts/scheduled-tasks/*.xml files, which carry richer settings —
    principal/run-level/idle policy — than this generic path expresses).

    macOS: writes a launchd plist to ~/Library/LaunchAgents (never into the
    repo) and loads it with `launchctl load -w`.

    Linux: writes a systemd --user service+timer unit to
    ~/.config/systemd/user (never into the repo) and enables it with
    `systemctl --user enable --now`. If systemd is unavailable, this does
    NOT silently no-op: it returns ok=False with the documented crontab
    fallback line for the caller to add by hand.
    """
    plat = _plat()
    if plat == WINDOWS:
        return False, (
            "platform_adapter.register_task does not handle Windows; "
            "use scripts/register_tasks.py (schtasks /Create /XML)"
        )

    if plat == MACOS:
        plist_path = _launchd_plist_path(name)
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        plist_path.write_text(_launchd_plist(name, command, interval_minutes), encoding="utf-8")
        result = subprocess.run(["launchctl", "load", "-w", str(plist_path)], capture_output=True, text=True)
        if result.returncode == 0:
            return True, f"loaded {plist_path} via launchctl"
        return False, f"wrote {plist_path} but launchctl load failed: {result.stderr.strip()}"

    if plat == LINUX:
        if not _systemd_user_available():
            cron_line = f"*/{interval_minutes} * * * * {' '.join(command)}"
            return False, (
                "systemd --user unavailable; nothing was registered. Documented fallback — "
                f"add this line with `crontab -e`: {cron_line}"
            )
        unit_dir = _systemd_user_dir()
        unit_dir.mkdir(parents=True, exist_ok=True)
        service = unit_dir / f"{name}.service"
        timer = unit_dir / f"{name}.timer"
        service.write_text(_systemd_service_unit(name, command, schedule_hint, interval_minutes), encoding="utf-8")
        timer.write_text(_systemd_timer_unit(name, interval_minutes), encoding="utf-8")
        result = subprocess.run(
            ["systemctl", "--user", "enable", "--now", f"{name}.timer"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return True, f"wrote {service} and {timer}, enabled via systemctl --user"
        return False, f"wrote {service} and {timer} but systemctl --user enable failed: {result.stderr.strip()}"

    return False, f"unsupported platform: {plat}"


def describe_register_command(name: str) -> str:
    """Human-runnable string describing how to (re)register this task."""
    plat = _plat()
    if plat == WINDOWS:
        return f'python -X utf8 scripts/register_tasks.py  # registers "{name}" from scripts/scheduled-tasks/{name}.xml'
    if plat == MACOS:
        return f'launchctl load -w "{_launchd_plist_path(name)}"'
    if plat == LINUX:
        if _systemd_user_available():
            return f"systemctl --user enable --now {name}.timer"
        return f'crontab -e  # systemd --user unavailable; add: */<interval_minutes> * * * * <command>'
    return f"unsupported platform: {plat}"


def query_task(name: str) -> dict:
    """Return a normalized dict: {"exists": bool|None, "last_result": str|None,
    "last_run": str|"unverified", "error": str (optional)}.

    "unverified" for last_run (or exists=None) means the query mechanism
    itself could not be reached (missing binary, permission, unparseable
    output) — distinct from a task that ran and reported a real failure.
    """
    plat = _plat()

    if plat == WINDOWS:
        try:
            raw = subprocess.run(
                ["schtasks", "/Query", "/TN", name, "/FO", "LIST", "/V"],
                capture_output=True, timeout=15,
            )
        except (FileNotFoundError, PermissionError, OSError) as e:
            return {"exists": None, "last_result": None, "last_run": "unverified", "error": str(e)}
        if raw.returncode != 0:
            err = (raw.stderr or raw.stdout).decode("cp932", errors="replace").strip()
            return {"exists": False, "last_result": None, "last_run": "unverified", "error": err}
        out = raw.stdout.decode("cp932", errors="replace")
        last_result = last_run = None
        for line in out.splitlines():
            low = line.strip().lower()
            if low.startswith("last result:"):
                last_result = line.split(":", 1)[1].strip()
            elif low.startswith("last run time:"):
                last_run = line.split(":", 1)[1].strip()
        return {"exists": True, "last_result": last_result, "last_run": last_run or "unverified"}

    if plat == MACOS:
        label = _launchd_label(name)
        try:
            raw = subprocess.run(["launchctl", "list", label], capture_output=True, text=True, timeout=15)
        except (FileNotFoundError, PermissionError, OSError) as e:
            return {"exists": None, "last_result": None, "last_run": "unverified", "error": str(e)}
        if raw.returncode != 0:
            return {"exists": False, "last_result": None, "last_run": "unverified",
                     "error": raw.stderr.strip() or raw.stdout.strip()}
        last_result = None
        for line in raw.stdout.splitlines():
            line = line.strip()
            if line.startswith('"LastExitStatus"'):
                last_result = line.split("=", 1)[-1].strip().rstrip(";").strip()
        # launchd's `list` output has no human last-run timestamp (that needs
        # log(1)/os_log) — report last_run as unverified rather than guessing.
        return {"exists": True, "last_result": last_result, "last_run": "unverified"}

    if plat == LINUX:
        if not _systemd_user_available():
            return {"exists": None, "last_result": None, "last_run": "unverified",
                     "error": "systemd --user unavailable; check the documented crontab fallback manually"}
        try:
            raw = subprocess.run(
                ["systemctl", "--user", "show", f"{name}.service",
                 "--property=Result,ExecMainStatus,ActiveEnterTimestamp"],
                capture_output=True, text=True, timeout=15,
            )
        except (FileNotFoundError, PermissionError, OSError) as e:
            return {"exists": None, "last_result": None, "last_run": "unverified", "error": str(e)}
        if raw.returncode != 0 or not raw.stdout.strip():
            return {"exists": False, "last_result": None, "last_run": "unverified",
                     "error": raw.stderr.strip() or "unit not found"}
        props = dict(line.split("=", 1) for line in raw.stdout.splitlines() if "=" in line)
        return {
            "exists": True,
            "last_result": props.get("Result"),
            "last_run": props.get("ActiveEnterTimestamp") or "unverified",
        }

    return {"exists": None, "last_result": None, "last_run": "unverified", "error": f"unsupported platform: {plat}"}


# --------------------------------------------------------------------------
# 3. SHELL
# --------------------------------------------------------------------------

def resolve_bash() -> str:
    """Return a real bash executable path, or "" to fail closed if none can
    be trusted (the caller must then error rather than silently pass).

    On POSIX, /bin/bash is used directly — the WSL-stub problem Windows has
    (a bash.exe on PATH that is actually the WSL launcher and silently does
    nothing) does not exist there."""
    if _plat() != WINDOWS:
        return "/bin/bash"

    candidates = [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ]
    for c in candidates:
        if Path(c).is_file():
            return c
    found = shutil.which("bash")
    if found and "windows\\system32" not in found.lower():
        return found
    return ""


# --------------------------------------------------------------------------
# 4. PATHS
# --------------------------------------------------------------------------

def default_backup_root() -> Path:
    return Path.home() / "backups" / "agentic-workspace-ledgers"


def health_dir() -> Path:
    return Path.home() / ".claude" / "health"


# --------------------------------------------------------------------------
# Self-test — exercises the POSIX branches (and re-confirms Windows) by
# forcing sys.platform, without doing any real filesystem/process I/O for
# the impersonated platforms (only the pure content generators + command
# strings are checked; register_task itself is not called under
# impersonation, since that would write real files under paths built from
# the *impersonated* platform's home-dir convention on top of this actual
# Windows filesystem).
# --------------------------------------------------------------------------

def _self_test() -> int:
    failures: list[str] = []

    def check(cond: bool, desc: str) -> None:
        print(f"{'PASS' if cond else 'FAIL'}: {desc}")
        if not cond:
            failures.append(desc)

    real_platform = sys.platform
    try:
        # --- darwin -----------------------------------------------------
        # NOTE: pathlib.Path picks its flavor (Windows/PosixPath) from os.name,
        # not sys.platform, so impersonating sys.platform alone does not change
        # how Path renders a "/a/b" string on this Windows box. PurePosixPath
        # is used here to construct POSIX-shaped test inputs deterministically
        # regardless of host OS — see the report for why this matters for real
        # POSIX code (it must not rely on sys.platform to pick a Path flavor).
        sys.platform = "darwin"
        cmd = describe_link_command(PurePosixPath("/Users/x/.claude/skills"), PurePosixPath("/Users/x/repo/skills"), "symlink")
        check(cmd == 'ln -s "/Users/x/repo/skills" "/Users/x/.claude/skills"', f"darwin describe_link_command: {cmd}")

        cmd2 = describe_remove_and_relink_command(PurePosixPath("/Users/x/.claude/agents"), PurePosixPath("/Users/x/repo/agents/claude"), "junction")
        check(cmd2 == 'rm "/Users/x/.claude/agents" && ln -s "/Users/x/repo/agents/claude" "/Users/x/.claude/agents"',
              f"darwin describe_remove_and_relink_command (junction collapses to symlink): {cmd2}")

        b = resolve_bash()
        check(b == "/bin/bash", f"darwin resolve_bash: {b}")

        reg = describe_register_command("agentic-weekly-health")
        check(reg == 'launchctl load -w "' + str(_launchd_plist_path("agentic-weekly-health")) + '"',
              f"darwin describe_register_command: {reg}")

        plist = _launchd_plist("agentic-weekly-health", [sys.executable, "-X", "utf8", "/repo/scripts/health_check.py"], 10080)
        check("<key>Label</key><string>com.agentic-workspace.agentic-weekly-health</string>" in plist,
              "darwin launchd plist contains Label")
        check(f"<key>StartInterval</key><integer>{10080 * 60}</integer>" in plist,
              "darwin launchd plist StartInterval is interval_minutes*60")
        check("<string>/repo/scripts/health_check.py</string>" in plist,
              "darwin launchd plist ProgramArguments contains the script path")

        q = query_task("agentic-weekly-health")  # launchctl.exe genuinely absent on this Windows box
        check(q["exists"] is None and q["last_run"] == "unverified",
              f"darwin query_task fails closed to unverified when launchctl is unreachable: {q}")

        # --- linux --------------------------------------------------------
        sys.platform = "linux"
        cmd = describe_link_command(PurePosixPath("/home/x/.claude/agents"), PurePosixPath("/home/x/repo/agents/claude"), "junction")
        check(cmd == 'ln -s "/home/x/repo/agents/claude" "/home/x/.claude/agents"',
              f"linux describe_link_command (junction collapses to symlink, no new kind invented): {cmd}")

        b = resolve_bash()
        check(b == "/bin/bash", f"linux resolve_bash: {b}")

        systemd_present = _systemd_user_available()
        reg = describe_register_command("agentic-ledger-backup")
        if systemd_present:
            check(reg == "systemctl --user enable --now agentic-ledger-backup.timer",
                  f"linux describe_register_command (systemd present): {reg}")
        else:
            check(reg.startswith("crontab -e"), f"linux describe_register_command fallback (no systemd): {reg}")

        svc = _systemd_service_unit("agentic-ledger-backup", [sys.executable, "-X", "utf8", "/repo/scripts/backup_ledgers.py"], "daily 03:00", 1440)
        check("ExecStart=" + sys.executable + " -X utf8 /repo/scripts/backup_ledgers.py" in svc,
              "linux systemd service unit ExecStart contains the command")
        timer = _systemd_timer_unit("agentic-ledger-backup", 1440)
        check("OnUnitActiveSec=1440min" in timer, "linux systemd timer unit has the declared interval")

        q = query_task("agentic-ledger-backup")
        if systemd_present:
            # systemctl exists on PATH but --user has no such unit on this Windows box's msys/WSL-adjacent tooling
            check(q["exists"] in (False, None), f"linux query_task with systemctl present but unit absent: {q}")
        else:
            check(q["exists"] is None and q["last_run"] == "unverified",
                  f"linux query_task fails closed when systemd --user is unavailable: {q}")

        # --- win32 (must be byte-identical to pre-adapter behavior) -------
        sys.platform = "win32"
        cmd = describe_link_command(Path(r"C:\Users\x\.claude\agents"), Path(r"C:\repo\agents\claude"), "junction")
        check(cmd == 'cmd /c mklink /J "C:\\Users\\x\\.claude\\agents" "C:\\repo\\agents\\claude"',
              f"win32 describe_link_command unchanged: {cmd}")

        cmd2 = describe_remove_and_relink_command(Path(r"C:\Users\x\.claude\skills"), Path(r"C:\repo\skills"), "symlink")
        check(cmd2 == 'cmd /c del "C:\\Users\\x\\.claude\\skills" && cmd /c mklink /D "C:\\Users\\x\\.claude\\skills" "C:\\repo\\skills"',
              f"win32 describe_remove_and_relink_command unchanged: {cmd2}")

        reg = describe_register_command("agentic-weekly-health")
        check("scripts/register_tasks.py" in reg, f"win32 describe_register_command still points at register_tasks.py: {reg}")

        b = resolve_bash()
        check(b == "" or Path(b).is_file(), f"win32 resolve_bash fail-closed or real path: {b!r}")
    finally:
        sys.platform = real_platform

    print()
    if failures:
        print(f"SELF-TEST FAIL ({len(failures)})")
        return 1
    print("SELF-TEST OK")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return _self_test()
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
