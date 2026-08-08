#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def parse_database(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("database must use NAME=ABSOLUTE_PATH")
    name, raw_path = value.split("=", 1)
    if not name or Path(name).name != name or not name.endswith(".sqlite3"):
        raise argparse.ArgumentTypeError("database NAME must be a safe .sqlite3 filename")
    source = Path(raw_path)
    if not source.is_absolute():
        raise argparse.ArgumentTypeError("database source must be absolute")
    return name, source


def backup_database(source: Path, destination: Path) -> dict:
    if not source.is_file():
        raise FileNotFoundError(f"database source not found: {source}")
    with tempfile.NamedTemporaryFile(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
    try:
        source_db = sqlite3.connect(str(source), timeout=30)
        destination_db = sqlite3.connect(str(temporary))
        try:
            source_db.backup(destination_db)
            integrity = destination_db.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            destination_db.close()
            source_db.close()
        if integrity != "ok":
            raise RuntimeError(f"backup integrity check failed: {integrity}")
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {
        "source": str(source),
        "backup": str(destination),
        "bytes": destination.stat().st_size,
        "integrity": "ok",
        "sha256": sha256_file(destination),
    }


def run(output_dir: Path, hooks: Path, databases: list[tuple[str, Path]]) -> dict:
    if not output_dir.is_absolute():
        raise ValueError("output directory must be absolute")
    output_dir.mkdir(parents=True, exist_ok=False)
    if not hooks.is_file():
        raise FileNotFoundError(f"hooks file not found: {hooks}")
    hooks_destination = output_dir / "hooks.pre-cutover.json"
    shutil.copy2(hooks, hooks_destination)
    results = [
        backup_database(source, output_dir / name) for name, source in databases
    ]
    return {
        "backup_dir": str(output_dir),
        "hooks": {
            "source": str(hooks),
            "backup": str(hooks_destination),
            "sha256": sha256_file(hooks_destination),
        },
        "databases": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create integrity-checked online backups before Skill ledger cutover"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--hooks", type=Path, required=True)
    parser.add_argument(
        "--database",
        type=parse_database,
        action="append",
        default=[],
        help="NAME.sqlite3=ABSOLUTE_SOURCE_PATH",
    )
    args = parser.parse_args()
    try:
        result = run(args.output_dir, args.hooks, args.database)
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "error_class": type(exc).__name__, "error": str(exc)},
                ensure_ascii=False,
            )
        )
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
