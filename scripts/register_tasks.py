#!/usr/bin/env python3
"""Register this workspace's Windows scheduled tasks.

Why this exists: `schtasks /Create /XML` only accepts UTF-16-encoded task XML,
but a UTF-16 file in git has no readable diff. So the repo keeps the XML as
UTF-8 and this script converts to UTF-16 in a temp file at registration time.

Task names come from config/wiring.json (kind == "scheduled-task"); the XML is
matched by `scripts/scheduled-tasks/<name>.xml`. A task declared in wiring with
no XML, or an XML with no wiring entry, is an error rather than a silent skip
(criterion: drift-coverage-completeness).

Run:  python -X utf8 scripts/register_tasks.py            # register/replace
      python -X utf8 scripts/register_tasks.py --check    # report only
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import platform_adapter  # noqa: E402  (same-dir sibling module)

ROOT = Path(__file__).resolve().parent.parent
TASK_DIR = ROOT / "scripts" / "scheduled-tasks"
# Registered by its own installer (skill-telemetry/scripts/configure_hooks.py),
# not from an XML in this repo. Declared here so the pairing check stays exact.
EXTERNALLY_MANAGED = {"Codex Skill Telemetry Optimizer"}


def declared_tasks() -> list[str]:
    wiring = json.loads((ROOT / "config" / "wiring.json").read_text(encoding="utf-8"))
    return [e["name"] for e in wiring["entries"] if e.get("kind") == "scheduled-task"]


def pair_tasks() -> list[tuple[str, Path]]:
    names = [n for n in declared_tasks() if n not in EXTERNALLY_MANAGED]
    xmls = {p.stem: p for p in TASK_DIR.glob("*.xml")}
    problems = [f"declared in wiring but no {TASK_DIR.name}/{n}.xml" for n in names if n not in xmls]
    problems += [f"{x}.xml present but not declared in wiring.json" for x in xmls if x not in names]
    if problems:
        for p in problems:
            print(f"ERROR: {p}")
        raise SystemExit(2)
    return [(n, xmls[n]) for n in names]


def register(name: str, xml: Path) -> int:
    # schtasks rejects UTF-8 XML ("cannot switch encoding"); convert on the fly.
    text = xml.read_text(encoding="utf-8").replace('encoding="UTF-8"', 'encoding="UTF-16"', 1)

    # A scheduler does not inherit the interactive PATH: a bare "python" in the
    # XML registers fine and then fails at first trigger with 0x80070002. Resolve
    # an absolute, upgrade-durable interpreter at registration time instead of
    # baking one machine's path into the repo.
    if "{{PYTHON}}" in text:
        python_path, why = platform_adapter.resolve_python()
        print(f"    interpreter: {python_path}  [{why}]")
        text = text.replace("{{PYTHON}}", python_path)
    tmp = Path(tempfile.gettempdir()) / f"{name}.utf16.xml"
    tmp.write_text(text, encoding="utf-16")  # utf-16 codec emits the BOM schtasks needs
    try:
        proc = subprocess.run(
            ["schtasks.exe", "/Create", "/TN", name, "/XML", str(tmp), "/F"],
            capture_output=True, encoding="oem", errors="replace",
        )
        print(f"[{'OK' if proc.returncode == 0 else 'FAIL'}] {name}: "
              f"{(proc.stdout or proc.stderr).strip()}")
        return proc.returncode
    finally:
        tmp.unlink(missing_ok=True)


def main() -> int:
    tasks = pair_tasks()
    if "--check" in sys.argv:
        for name, xml in tasks:
            print(f"would register: {name}  <- {xml.relative_to(ROOT)}")
        return 0
    return 0 if all(register(n, x) == 0 for n, x in tasks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
