#!/usr/bin/env python3
"""Body-free health ledger and non-mutating spool auditor."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

LEDGERS = ("skill-telemetry", "failure-learning", "feedback-learning")
FORBIDDEN_KEYS = {"prompt", "response", "content", "message", "input", "output", "raw", "tool_output", "exception", "traceback"}
ALLOWED_KEYS = {"auth_tag", "auth_version", "event_id", "event_type", "failure", "hook", "model_class", "observed_at", "repo_hash", "session_hash", "turn_hash", "stable_correlation", "correlation_id", "skills", "version", "idempotency_key", "operation_class", "safety", "tool_name", "versions", "evidence_role", "explicitness", "feedback_type", "impact", "persistence_requested", "reaction_signature", "source_kind", "subject_class", "subject_kind", "theme_key", "valence", "sequence"}
HASH_KEYS = {"auth_tag", "event_id", "idempotency_key", "repo_hash", "session_hash", "turn_hash", "correlation_id", "reaction_signature"}
PATH_KEYS = {"path", "file", "filename", "source_path", "draft_path", "output_path"}
MAX_EVENT_BYTES = 512 * 1024
LOCK_LEASE_SECONDS = 60


def root_dir() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def safe_lock(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, json.dumps({"pid": os.getpid(), "created_at": now()}).encode("utf-8"))
        os.close(fd)
    except FileExistsError:
        try:
            if time.time() - path.stat().st_mtime <= LOCK_LEASE_SECONDS:
                raise RuntimeError("lock_busy")
            os.replace(path, path.with_name(path.name + f".stale.{os.getpid()}"))
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, json.dumps({"pid": os.getpid(), "created_at": now()}).encode("utf-8"))
            os.close(fd)
        except RuntimeError:
            raise
        except Exception:
            raise RuntimeError("lock_busy")


def unlock(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def valid_scalar(key: str, value: object) -> bool:
    if key in PATH_KEYS or key in FORBIDDEN_KEYS:
        return False
    if isinstance(value, str):
        if len(value) > 256 or any(ord(ch) < 32 for ch in value):
            return False
        if key in HASH_KEYS and (len(value) < 16 or any(ch not in "0123456789abcdef" for ch in value.lower())):
            return False
        if ("\\" in value or "/" in value) and key != "observed_at":
            return False
        return True
    if isinstance(value, (bool, int)) or value is None:
        return True
    if isinstance(value, list):
        return all(valid_scalar(key, item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and valid_scalar(k, v) for k, v in value.items())
    return False


def scan(base: Path) -> dict:
    seen: set[str] = set()
    totals = {name: 0 for name in LEDGERS}
    sequences: dict[str, list[int]] = {name: [] for name in LEDGERS}
    malformed = duplicates = orphan = gaps = oversized = unreadable = 0
    oldest: str | None = None
    missing_ledgers = 0
    for name in LEDGERS:
        spool = base / name / "spool"
        if not spool.is_dir():
            missing_ledgers += 1
            continue
        for item in sorted(spool.rglob("*.json"), key=lambda p: str(p).casefold()):
            totals[name] += 1
            try:
                if item.is_symlink() or item.stat().st_size > MAX_EVENT_BYTES:
                    if item.stat().st_size > MAX_EVENT_BYTES:
                        oversized += 1
                    else:
                        malformed += 1
                    continue
                value = json.loads(item.read_text(encoding="utf-8"))
                if not isinstance(value, dict) or not isinstance(value.get("event_id"), str):
                    raise ValueError
                if not set(value).issubset(ALLOWED_KEYS) or not all(valid_scalar(k, v) for k, v in value.items()):
                    raise ValueError
                observed = value.get("observed_at")
                if not isinstance(observed, str):
                    raise ValueError
                oldest = observed if oldest is None else min(oldest, observed)
                event_id = value["event_id"]
                duplicates += int(event_id in seen)
                seen.add(event_id)
                orphan += int(not value.get("session_hash") and not value.get("correlation_id"))
                sequence = value.get("sequence")
                if isinstance(sequence, int):
                    if sequence < 1:
                        gaps += 1
                    else:
                        sequences[name].append(sequence)
            except PermissionError:
                unreadable += 1
            except Exception:
                malformed += 1
    for values in sequences.values():
        if values:
            gaps += max(values) - min(values) + 1 - len(set(values))
    return {"totals": totals, "total": sum(totals.values()), "oldest_observed_at": oldest,
            "malformed": malformed, "duplicates": duplicates, "orphans": orphan, "gaps": gaps,
            "oversized": oversized, "unreadable": unreadable, "missing_ledgers": missing_ledgers,
            "body_free": True}


def state(snapshot: dict, *, stale_hours: float, hook_observed: bool, episodic_observed: bool) -> str:
    if snapshot["malformed"] or snapshot["duplicates"] or snapshot["gaps"] or snapshot["oversized"] or snapshot["unreadable"]:
        return "DEGRADED"
    if snapshot["missing_ledgers"] == len(LEDGERS) or not hook_observed or not episodic_observed:
        return "INCOMPLETE"
    if not snapshot["total"]:
        return "NO_DATA"
    oldest = snapshot.get("oldest_observed_at")
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(oldest)).total_seconds() / 3600 if oldest else 0
        if age > stale_hours:
            return "STALE"
    except ValueError:
        return "DEGRADED"
    return "INCOMPLETE" if snapshot["orphans"] else "HEALTHY"


def append_record(ledger: Path, record: dict) -> None:
    ledger.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    existing = ledger.read_bytes() if ledger.exists() else b""
    fd, temporary = tempfile.mkstemp(prefix=".health-", suffix=".tmp", dir=ledger.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(existing + payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, ledger)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description="Body-free local Skill health auditor")
    parser.add_argument("command", choices=("baseline", "health", "dry-run-drain"))
    parser.add_argument("--root", default=None)
    parser.add_argument("--budget", type=int, default=5000)
    parser.add_argument("--stale-hours", type=float, default=24.0)
    parser.add_argument("--hook-observed", action="store_true")
    parser.add_argument("--episodic-observed", action="store_true")
    args = parser.parse_args()
    base = Path(args.root).expanduser().resolve() if args.root else root_dir()
    ledger = base / "skill-telemetry" / "health-ledger.jsonl"
    lock = base / "skill-telemetry" / ".health.lock"
    try:
        safe_lock(lock)
        try:
            snap = scan(base)
            if args.command == "dry-run-drain":
                snap.update({"budget": max(0, args.budget), "budget_exhausted": snap["total"] > max(0, args.budget), "receipt_id": digest(now() + json.dumps(snap, sort_keys=True))[:24], "operation": "dry-run", "at": now()})
                append_record(ledger, {"kind": "drain_receipt", **snap})
                output = {"status": "dry-run", **snap}
            else:
                current = {"state": state(snap, stale_hours=args.stale_hours, hook_observed=args.hook_observed, episodic_observed=args.episodic_observed), "at": now(), **snap}
                append_record(ledger, {"kind": args.command, **current})
                output = {"status": "recorded", **current}
        finally:
            unlock(lock)
        print(json.dumps(output, separators=(",", ":")))
        return 0
    except RuntimeError as error:
        print(json.dumps({"status": "blocked", "state": "BLOCKED", "error_code": str(error)}, separators=(",", ":")))
        return 2
    except Exception:
        print(json.dumps({"status": "failed", "error_code": "health_failed"}, separators=(",", ":")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
