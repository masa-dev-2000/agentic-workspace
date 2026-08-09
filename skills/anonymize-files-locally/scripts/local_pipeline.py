#!/usr/bin/env python3
"""Metadata-only orchestration for local format adaptation and approved rebuilds."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from path_guard import safe_path, same_file

ADAPTER = Path(__file__).with_name("local_format_adapter.py")
REBUILDER = Path(__file__).with_name("local_format_rebuilder.py")
REBUILDABLE = {".xlsx", ".docx"}
ADAPTABLE = REBUILDABLE | {".pdf"}


def emit(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def run_child(command: list[str]) -> dict:
    result = subprocess.run(command, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", check=False)
    if result.returncode != 0:
        raise RuntimeError("child_failed")
    for line in reversed(result.stdout.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise RuntimeError("child_metadata_missing")


def prepare(args: argparse.Namespace) -> None:
    source = safe_path(args.input, must_exist=True)
    extension = source.suffix.lower()
    if extension not in ADAPTABLE:
        emit({"status": "ready", "format": extension.lstrip("."),
              "normalized_path": str(source), "next": "anonymize"})
        return
    output = safe_path(args.output, output=True)
    if output == source:
        raise ValueError("output_overwrite_forbidden")
    output.parent.mkdir(parents=True, exist_ok=True)
    metadata = run_child([sys.executable, str(ADAPTER), "extract",
                          "--input", str(source), "--output", str(output)])
    emit({"status": "prepared", "format": extension.lstrip("."),
          "normalized_path": str(output), "adapter": metadata})


def rebuild(args: argparse.Namespace) -> None:
    source = safe_path(args.input, must_exist=True)
    normalized = safe_path(args.normalized, must_exist=True)
    output = safe_path(args.output, output=True)
    extension = source.suffix.lower()
    if extension not in REBUILDABLE:
        raise ValueError("format_rebuild_not_supported")
    if output == source or same_file(output, source) or same_file(output, normalized):
        raise ValueError("output_overwrite_forbidden")
    manifest = safe_path(args.manifest, must_exist=True)
    record = json.loads(manifest.read_text(encoding="utf-8"))
    if (record.get("schema_version") != 1 or record.get("status") != "approved"
            or not record.get("reviewed_at")
            or Path(record.get("source_path", "")).resolve() != source
            or Path(record.get("draft_path", "")).resolve() != normalized
            or record.get("format") != extension.lstrip(".")):
        raise ValueError("human_approval_required")
    if record.get("source_sha256") != digest(source) or record.get("draft_sha256") != digest(normalized):
        raise ValueError("stale_artifact")
    output.parent.mkdir(parents=True, exist_ok=True)
    metadata = run_child([sys.executable, str(REBUILDER), "rebuild",
                          "--input", str(source), "--normalized", str(normalized),
                          "--output", str(output)])
    emit({"status": "rebuilt", "format": extension.lstrip("."),
          "output_path": str(output), "rebuilder": metadata})


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Local anonymization format pipeline")
    commands = result.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare")
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.set_defaults(handler=prepare)
    r = commands.add_parser("rebuild")
    r.add_argument("--input", required=True)
    r.add_argument("--normalized", required=True)
    r.add_argument("--manifest", required=True)
    r.add_argument("--output", required=True)
    r.set_defaults(handler=rebuild)
    return result


def main() -> int:
    try:
        args = parser().parse_args()
        args.handler(args)
        return 0
    except ValueError as error:
        emit({"status": "failed", "error_code": str(error)})
        return 2
    except Exception:
        emit({"status": "failed", "error_code": "pipeline_failed"})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
