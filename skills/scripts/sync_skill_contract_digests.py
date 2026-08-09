from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import yaml

from validate_skill_registry import (
    DEFAULT_REGISTRY,
    contract_content_digest,
)


ENTRY_RE = re.compile(
    r"(?ms)^  - key: (?P<key>[^\r\n]+)\r?\n(?P<body>.*?)(?=^  - key: |\Z)"
)
DIGEST_RE = re.compile(r"(?m)^    contractContentDigest: .+$")
FINGERPRINT_RE = re.compile(r"(?m)^    contractFingerprint: .+$")


def synchronize(path: Path, *, write: bool) -> dict[str, object]:
    text = path.read_text(encoding="utf-8-sig")
    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict) or not isinstance(loaded.get("skills"), list):
        raise ValueError("registry must contain a skills list")
    expected = {
        skill["key"]: contract_content_digest(skill)
        for skill in loaded["skills"]
        if isinstance(skill, dict) and isinstance(skill.get("key"), str)
    }
    seen: set[str] = set()
    changed: list[str] = []

    def replace_entry(match: re.Match[str]) -> str:
        key = match.group("key").strip()
        entry = match.group(0)
        digest = expected.get(key)
        if digest is None:
            return entry
        seen.add(key)
        digest_line = f"    contractContentDigest: {digest}"
        current = DIGEST_RE.search(entry)
        if current:
            updated = DIGEST_RE.sub(digest_line, entry, count=1)
        else:
            updated = FINGERPRINT_RE.sub(
                lambda fingerprint: f"{fingerprint.group(0)}\n{digest_line}",
                entry,
                count=1,
            )
        if updated != entry:
            changed.append(key)
        return updated

    updated_text = ENTRY_RE.sub(replace_entry, text)
    missing = sorted(set(expected) - seen)
    if missing:
        raise ValueError(f"registry entries were not found in source text: {missing}")
    if write and updated_text != text:
        path.write_text(updated_text, encoding="utf-8", newline="\n")
    return {
        "valid": not changed,
        "changed": changed,
        "count": len(expected),
        "written": write and updated_text != text,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check or mechanically refresh Skill contract-content digests."
    )
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    result = synchronize(args.registry.resolve(), write=args.write)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] or result["written"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
