#!/usr/bin/env python3
"""Regenerate the machine-generated blocks inside the docs.

A doc that merely *claims* to be generated still drifts — docs/OPERATIONS.md said
its wiring table "cannot drift from config/wiring.json" while actually holding a
hand-pasted copy. Anything a machine can derive belongs between markers:

    <!-- generated:<id> -->
    ...regenerated content, do not edit by hand...
    <!-- /generated:<id> -->

Prose outside the markers stays human-owned: the *why*, the tradeoffs, the
runbooks. Only the derivable parts are synced here.

Run: python -X utf8 scripts/sync_generated_docs.py           # rewrite blocks
     python -X utf8 scripts/sync_generated_docs.py --check   # fail if stale
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def gen_wiring_table() -> str:
    """The wiring map, straight out of the tool that owns config/wiring.json."""
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(ROOT / "scripts" / "bootstrap_workspace.py"), "--markdown"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"bootstrap_workspace.py --markdown failed: {proc.stderr.strip()[:300]}")
    return proc.stdout.strip()


def gen_skill_count() -> str:
    skills = sorted(
        d.name for d in (ROOT / "skills").iterdir()
        if d.is_dir() and (d / "SKILL.md").is_file()
    )
    return f"{len(skills)} skills are wired into every runtime. Verify with " \
           f"`python -X utf8 scripts/list_skill_sources.py --names`."


# doc path -> {block id: generator}
BLOCKS = {
    ROOT / "docs" / "OPERATIONS.md": {
        "wiring-table": gen_wiring_table,
        "skill-count": gen_skill_count,
    },
}


def apply(path: Path, block_id: str, content: str, text: str) -> tuple[str, bool]:
    open_m, close_m = f"<!-- generated:{block_id} -->", f"<!-- /generated:{block_id} -->"
    pattern = re.compile(
        re.escape(open_m) + r".*?" + re.escape(close_m), re.S
    )
    if not pattern.search(text):
        raise RuntimeError(f"{path.name}: no marker pair for '{block_id}' — add {open_m} / {close_m}")
    new_block = f"{open_m}\n{content}\n{close_m}"
    updated = pattern.sub(lambda _: new_block, text, count=1)
    return updated, updated != text


def main() -> int:
    check = "--check" in sys.argv
    stale: list[str] = []
    for path, blocks in BLOCKS.items():
        if not path.is_file():
            print(f"ERROR: missing doc {path}")
            return 2
        text = original = path.read_text(encoding="utf-8")
        for block_id, generator in blocks.items():
            text, changed = apply(path, block_id, generator(), text)
            if changed:
                stale.append(f"{path.relative_to(ROOT)}:{block_id}")
        if text != original and not check:
            path.write_text(text, encoding="utf-8")
    if stale:
        if check:
            print("STALE generated blocks (run without --check to regenerate):")
            for s in stale:
                print(f"  - {s}")
            return 1
        print("regenerated: " + ", ".join(stale))
    else:
        print("generated blocks up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
