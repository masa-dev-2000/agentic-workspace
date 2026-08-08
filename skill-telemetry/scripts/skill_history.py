from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = ROOT / "skill-registry.yaml"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def state_root() -> Path:
    override = os.environ.get("CODEX_SKILL_TELEMETRY_HOME")
    if override:
        return Path(override).resolve() / "history"
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    return codex_home.resolve() / "skill-telemetry" / "history"


def load_registry_entry(skill: str, registry: Path) -> dict:
    data = yaml.safe_load(registry.read_text(encoding="utf-8-sig"))
    for entry in data.get("skills", []):
        if entry.get("key") == skill:
            return entry
    raise ValueError(f"skill is not registered: {skill}")


def skill_manifest(skill_dir: Path) -> list[dict[str, str]]:
    rows = []
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        rows.append(
            {
                "path": path.relative_to(skill_dir).as_posix(),
                "sha256": sha256_file(path),
            }
        )
    return rows


def event_id(event: dict) -> str:
    identity = {
        key: event.get(key)
        for key in (
            "event_type",
            "skill",
            "effective_date",
            "content_fingerprint",
            "evidence_fingerprint",
        )
    }
    return sha256_bytes(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


@contextmanager
def ledger_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".lock"
    handle = lock_path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        yield
    finally:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        handle.close()


def append_event(event: dict, root: Path) -> bool:
    event = dict(event)
    event["event_id"] = event_id(event)
    ledger = root / "events.jsonl"
    with ledger_lock(root):
        existing = set()
        if ledger.exists():
            for line in ledger.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    existing.add(json.loads(line)["event_id"])
        if event["event_id"] in existing:
            return False
        with ledger.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return True


def snapshot(skill: str, registry: Path, root: Path) -> dict:
    entry = load_registry_entry(skill, registry)
    skill_dir = (ROOT / entry["path"]).resolve()
    manifest = skill_manifest(skill_dir)
    content_fingerprint = sha256_bytes(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    event = {
        "event_type": "snapshot",
        "skill": skill,
        "observed_at": utc_now(),
        "effective_date": datetime.now(timezone.utc).date().isoformat(),
        "provenance": "observed",
        "skill_version": str(entry.get("version", "")),
        "contract_fingerprint": str(entry.get("contractFingerprint", "")),
        "contract_content_digest": str(entry.get("contractContentDigest", "")),
        "content_fingerprint": content_fingerprint,
        "evidence_fingerprint": "",
        "file_manifest": manifest,
    }
    inserted = append_event(event, root)
    return {"inserted": inserted, "event_id": event_id(event), **event}


def backfill(skill: str, evidence_root: Path, root: Path) -> dict:
    inserted = 0
    discovered = 0
    if not evidence_root.exists():
        return {"discovered": 0, "inserted": 0}
    for period in sorted(path for path in evidence_root.iterdir() if path.is_dir()):
        files = sorted(path for path in period.rglob("*") if path.is_file())
        if not files:
            continue
        discovered += 1
        manifest = [
            {
                "path_hash": sha256_bytes(
                    path.relative_to(period).as_posix().encode("utf-8")
                ),
                "sha256": sha256_file(path),
            }
            for path in files
        ]
        evidence_fingerprint = sha256_bytes(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        event = {
            "event_type": "historical-evidence",
            "skill": skill,
            "observed_at": utc_now(),
            "effective_date": period.name,
            "provenance": "inferred",
            "skill_version": "",
            "contract_fingerprint": "",
            "contract_content_digest": "",
            "content_fingerprint": "",
            "evidence_fingerprint": evidence_fingerprint,
            "evidence_file_count": len(files),
            "limitations": [
                "Historical Skill content was not recoverable from evidence artifacts alone",
                "The event proves evidence existence, not exact causality or version content",
            ],
        }
        if append_event(event, root):
            inserted += 1
    return {"discovered": discovered, "inserted": inserted}


def history(skill: str, root: Path) -> list[dict]:
    ledger = root / "events.jsonl"
    if not ledger.exists():
        return []
    events = [
        json.loads(line)
        for line in ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return sorted(
        [event for event in events if event.get("skill") == skill],
        key=lambda event: (event.get("effective_date", ""), event.get("observed_at", "")),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Append and inspect privacy-safe Skill history.")
    sub = parser.add_subparsers(dest="command", required=True)
    snap = sub.add_parser("snapshot")
    snap.add_argument("--skill", required=True)
    snap.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    fill = sub.add_parser("backfill")
    fill.add_argument("--skill", required=True)
    fill.add_argument("--evidence-root", type=Path, required=True)
    show = sub.add_parser("history")
    show.add_argument("--skill", required=True)
    args = parser.parse_args()
    root = state_root()
    if args.command == "snapshot":
        result = snapshot(args.skill, args.registry.resolve(), root)
    elif args.command == "backfill":
        result = backfill(args.skill, args.evidence_root.resolve(), root)
    else:
        result = {"skill": args.skill, "events": history(args.skill, root)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
