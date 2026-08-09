#!/usr/bin/env python3
"""Back up the local learning ledgers declared in config/wiring.json (kind=="ledger").

Why this exists: the ledgers under ~/.codex/{failure-learning,feedback-learning,
skill-telemetry,adaptive-orchestrator} (~45MB, sqlite + HMAC key files) exist only
on this machine. The GitHub repo is PUBLIC, so backups must land outside every git
work tree, never inside it.

Modes:
  (default)   run a real backup: copy ledgers to AGENTIC_BACKUP_ROOT (or the
              default under %USERPROFILE%\\backups\\agentic-workspace-ledgers),
              write manifest.json, prune old runs.
  --check     read-only: validate sources, destination safety, and free space.
              Writes nothing.

Run: python -X utf8 scripts/backup_ledgers.py [--check]
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

TOOL_VERSION = "1.0.0"

ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()
CODEX_HOME = HOME / ".codex"

# Historical dead snapshots dominate ledger size and are already-dead copies of
# earlier states (pre-migration/pre-repair backups the ledgers themselves made).
# They are not the live ledger data and are deliberately excluded from backup.
# This exclusion must stay written down here, never silent.
DEAD_SNAPSHOT_PATTERNS = ("*.pre-*", "*.before-reconcile-*", "telemetry.pre-*")

SQLITE_BUSY_RETRY_DELAY_SECONDS = 2
SQLITE_BACKUP_TIMEOUT_SECONDS = 30

RETENTION_KEEP_LAST_N_RUNS = 7


def expand(p: str) -> Path:
    return Path(p.replace("~", str(HOME), 1)) if p.startswith("~") else (ROOT / p)


def load_declared_ledgers() -> dict[str, Path]:
    """id -> live path, for every kind=="ledger" entry in config/wiring.json."""
    data = json.loads((ROOT / "config" / "wiring.json").read_text(encoding="utf-8"))
    out: dict[str, Path] = {}
    for entry in data.get("entries", []):
        if entry.get("kind") == "ledger":
            out[entry["id"]] = expand(entry["live"])
    return out


def discover_undeclared_ledgers(declared: dict[str, Path]) -> list[Path]:
    """Glob ~/.codex/*/ for any dir containing *.sqlite3 or *.db not covered by
    a declared ledger root. An undeclared ledger must break the run rather than
    be silently unbacked (active criterion drift-coverage-completeness)."""
    declared_dirs = {p.resolve() for p in declared.values() if p.exists()}
    undeclared: set[Path] = set()
    if not CODEX_HOME.is_dir():
        return []
    for child in CODEX_HOME.iterdir():
        if not child.is_dir():
            continue
        resolved = child.resolve()
        if resolved in declared_dirs:
            continue
        try:
            hits = list(child.rglob("*.sqlite3")) or list(child.rglob("*.db"))
        except OSError:
            # Broken symlink / unreadable path (e.g. plugin cache junk) — not a
            # ledger concern; skip rather than crash the backup run.
            continue
        if hits:
            undeclared.add(resolved)
    return sorted(undeclared)


def is_dead_snapshot(name: str) -> bool:
    import fnmatch

    return any(fnmatch.fnmatch(name, pat) for pat in DEAD_SNAPSHOT_PATTERNS)


def _is_real_git_marker(git_path: Path) -> bool:
    """A worktree's .git is a file containing 'gitdir: ...'; a real repo's .git
    is a directory with a HEAD file. Guards against a stray/empty .git dir
    (not an actual repo) being mistaken for one."""
    if git_path.is_file():
        return True
    if git_path.is_dir():
        return (git_path / "HEAD").exists()
    return False


def find_git_ancestor(path: Path) -> Path | None:
    """Walk path and its parents; return the first ancestor with a real .git
    marker (file or dir), or None."""
    candidates = [path] + list(path.parents)
    for candidate in candidates:
        git_path = candidate / ".git"
        if git_path.exists() and _is_real_git_marker(git_path):
            return candidate
    return None


def default_backup_root() -> Path:
    return HOME / "backups" / "agentic-workspace-ledgers"


def resolve_destination() -> Path:
    root = Path(os.environ.get("AGENTIC_BACKUP_ROOT", str(default_backup_root())))
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return root / stamp


def assert_destination_safe(dest: Path) -> None:
    """HARD ASSERT: dest must not be inside the repo root, and not inside any
    git work tree. This is the mechanical guard against a public-repo leak.
    Deliberately not implemented with the `assert` statement so it cannot be
    stripped by python -O."""
    resolved = dest.resolve() if dest.exists() else dest
    # Compare textually against repo root even if dest doesn't exist yet.
    try:
        resolved.relative_to(ROOT.resolve())
        print(f"ABORT: destination {resolved} is inside the repo root {ROOT}")
        sys.exit(3)
    except ValueError:
        pass
    hit = find_git_ancestor(resolved)
    if hit is not None:
        print(f"ABORT: destination {resolved} is inside a git work tree (found .git at {hit})")
        sys.exit(3)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def backup_sqlite(src: Path, dest: Path) -> tuple[str, int, str | None]:
    """Online-safe copy via sqlite3 .backup(). One retry on SQLITE_BUSY.
    Returns (status, bytes, sha256)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in (1, 2):
        try:
            src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=SQLITE_BACKUP_TIMEOUT_SECONDS)
            try:
                dest_conn = sqlite3.connect(str(dest), timeout=SQLITE_BACKUP_TIMEOUT_SECONDS)
                try:
                    src_conn.backup(dest_conn)
                finally:
                    dest_conn.close()
            finally:
                src_conn.close()
            size = dest.stat().st_size
            return "ok", size, sha256_of(dest)
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() or "busy" in str(e).lower():
                if attempt == 1:
                    time.sleep(SQLITE_BUSY_RETRY_DELAY_SECONDS)
                    continue
                if dest.exists():
                    dest.unlink()
                return "skipped-busy", 0, None
            raise
    return "skipped-busy", 0, None  # pragma: no cover


def plain_copy(src: Path, dest: Path) -> tuple[str, int, str]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return "ok", dest.stat().st_size, sha256_of(dest)


SQLITE_EXTS = {".sqlite3", ".db"}
WAL_SHM_SUFFIXES = ("-wal", "-shm")


def run_backup(declared: dict[str, Path], run_dir: Path, check_only: bool) -> tuple[list[dict], bool]:
    manifest: list[dict] = []
    had_busy_failure = False

    for ledger_id, root in declared.items():
        if not root.exists():
            manifest.append({
                "source": str(root), "ledger": ledger_id, "bytes": 0, "sha256": None,
                "sensitive": False, "status": "missing-source", "tool_version": TOOL_VERSION,
            })
            continue
        for path in sorted(root.rglob("*")):
            if path.is_dir():
                continue
            rel = path.relative_to(root)
            dest = run_dir / ledger_id / rel

            if path.name.endswith(WAL_SHM_SUFFIXES):
                manifest.append({
                    "source": str(path), "ledger": ledger_id, "bytes": 0, "sha256": None,
                    "sensitive": False, "status": "skipped-companion", "tool_version": TOOL_VERSION,
                })
                continue

            if is_dead_snapshot(path.name):
                manifest.append({
                    "source": str(path), "ledger": ledger_id, "bytes": 0, "sha256": None,
                    "sensitive": False, "status": "skipped-dead-snapshot", "tool_version": TOOL_VERSION,
                })
                continue

            sensitive = path.suffix == ".key"

            if check_only:
                manifest.append({
                    "source": str(path), "ledger": ledger_id, "bytes": path.stat().st_size, "sha256": None,
                    "sensitive": sensitive, "status": "would-copy", "tool_version": TOOL_VERSION,
                })
                continue

            if path.suffix in SQLITE_EXTS:
                status, size, digest = backup_sqlite(path, dest)
                if status == "skipped-busy":
                    had_busy_failure = True
            else:
                status, size, digest = plain_copy(path, dest)

            manifest.append({
                "source": str(path), "ledger": ledger_id, "bytes": size, "sha256": digest,
                "sensitive": sensitive, "status": status, "tool_version": TOOL_VERSION,
            })

    return manifest, had_busy_failure


def prune_old_runs(backup_root: Path, keep_current: Path) -> list[str]:
    """Retention: keep last N runs plus the first run of each month; prune the rest."""
    if not backup_root.is_dir():
        return []
    run_dirs = sorted(
        (d for d in backup_root.iterdir() if d.is_dir() and d != keep_current),
        key=lambda d: d.name,
    )
    all_runs = run_dirs + [keep_current]
    keep: set[Path] = set(all_runs[-RETENTION_KEEP_LAST_N_RUNS:])

    by_month: dict[str, Path] = {}
    for d in all_runs:
        month = d.name[:6]  # YYYYMM from YYYYMMDDTHHMMSSZ
        if month not in by_month:
            by_month[month] = d
    keep |= set(by_month.values())

    pruned: list[str] = []
    for d in run_dirs:
        if d not in keep:
            shutil.rmtree(d)
            pruned.append(d.name)
    return pruned


def check_free_space(dest: Path) -> tuple[int, Path]:
    probe = dest
    while not probe.exists():
        if probe.parent == probe:
            break
        probe = probe.parent
    usage = shutil.disk_usage(probe)
    return usage.free, probe


def main() -> int:
    check_only = "--check" in sys.argv

    declared = load_declared_ledgers()
    if not declared:
        print("ABORT: no kind==\"ledger\" entries found in config/wiring.json")
        return 3

    undeclared = discover_undeclared_ledgers(declared)
    if undeclared:
        print(f"ABORT: {len(undeclared)} undeclared ledger dir(s) found under {CODEX_HOME} "
              "(contain *.sqlite3/*.db but are not kind==\"ledger\" in config/wiring.json):")
        for d in undeclared:
            print(f"  - {d}")
        print("Add them to config/wiring.json (human-authored, see config/wiring.schema.md) "
              "or confirm they are out of scope, before this backup can run.")
        return 3

    missing = [str(p) for p in declared.values() if not p.exists()]
    if missing:
        print("ABORT: declared ledger source(s) missing:")
        for m in missing:
            print(f"  - {m}")
        return 3

    dest_root = Path(os.environ.get("AGENTIC_BACKUP_ROOT", str(default_backup_root())))
    run_dir = resolve_destination()
    assert_destination_safe(run_dir)
    assert_destination_safe(dest_root)

    free_bytes, probe = check_free_space(run_dir)
    print(f"Destination: {run_dir}")
    print(f"Free space at {probe}: {free_bytes / (1024**3):.2f} GiB")

    if check_only:
        manifest, _ = run_backup(declared, run_dir, check_only=True)
        would_copy = [m for m in manifest if m["status"] == "would-copy"]
        total_bytes = sum(m["bytes"] for m in would_copy)
        skipped_dead = sum(1 for m in manifest if m["status"] == "skipped-dead-snapshot")
        skipped_companion = sum(1 for m in manifest if m["status"] == "skipped-companion")
        print(f"CHECK OK: {len(declared)} declared ledger(s), {len(would_copy)} file(s) would be backed up "
              f"({total_bytes / (1024**2):.2f} MiB), {skipped_dead} dead-snapshot file(s) skipped, "
              f"{skipped_companion} wal/shm companion(s) skipped. Nothing written.")
        return 0

    run_dir.mkdir(parents=True, exist_ok=True)
    manifest, had_busy_failure = run_backup(declared, run_dir, check_only=False)

    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps({"run": run_dir.name, "tool_version": TOOL_VERSION, "entries": manifest},
                                         indent=2, ensure_ascii=False), encoding="utf-8")

    ok_count = sum(1 for m in manifest if m["status"] == "ok")
    total_bytes = sum(m["bytes"] for m in manifest if m["status"] == "ok")
    print(f"Backed up {ok_count} file(s), {total_bytes / (1024**2):.2f} MiB, manifest: {manifest_path}")

    for status_name in ("skipped-busy", "skipped-dead-snapshot", "skipped-companion", "missing-source"):
        n = sum(1 for m in manifest if m["status"] == status_name)
        if n:
            print(f"  {status_name}: {n}")

    pruned = prune_old_runs(dest_root, run_dir)
    if pruned:
        print(f"Pruned {len(pruned)} old run(s): {', '.join(pruned)}")

    if had_busy_failure:
        print("FAIL: one or more sqlite sources were busy on both attempts (see status=skipped-busy in manifest)")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
