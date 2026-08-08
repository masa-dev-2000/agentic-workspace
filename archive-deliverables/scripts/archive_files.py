from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def inside(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def collision_safe(destination: Path) -> Path:
    if not destination.exists():
        return destination
    counter = 2
    while True:
        candidate = destination.with_name(
            f"{destination.stem}__{counter}{destination.suffix}"
        )
        if not candidate.exists():
            return candidate
        counter += 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--archive-label", required=True)
    parser.add_argument("--files", nargs="+", required=True)
    args = parser.parse_args()

    base = Path(args.base).resolve(strict=True)
    archive = (base / "archive" / args.archive_label).resolve()
    if not inside(archive, base):
        raise SystemExit("Archive destination escapes the base directory.")
    archive.mkdir(parents=True, exist_ok=True)

    moved: list[dict[str, str]] = []
    for supplied in args.files:
        source = (base / supplied).resolve(strict=True)
        if not inside(source, base) or source == base:
            raise SystemExit(f"Source escapes the base directory: {supplied}")
        if not source.is_file():
            raise SystemExit(f"Source is not a file: {supplied}")
        destination = collision_safe(archive / source.name)
        shutil.move(str(source), str(destination))
        moved.append({"source": str(source), "destination": str(destination)})

    print(json.dumps({"base": str(base), "archive": str(archive), "moved": moved},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
