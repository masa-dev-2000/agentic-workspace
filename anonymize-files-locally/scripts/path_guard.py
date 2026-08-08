"""Fail-closed local path checks shared by the format tools."""
from __future__ import annotations

import os
from pathlib import Path

REPARSE_POINT = 0x0400


def _is_reparse(path: Path) -> bool:
    try:
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & REPARSE_POINT)
    except (FileNotFoundError, OSError):
        return False


def safe_path(value: str, *, must_exist: bool = False, output: bool = False) -> Path:
    raw = Path(value).expanduser()
    current = raw
    while True:
        if os.path.lexists(current) and (current.is_symlink() or _is_reparse(current)):
            raise ValueError("symlink_or_reparse_forbidden")
        if current.parent == current:
            break
        current = current.parent
    resolved = raw.resolve(strict=must_exist)
    if must_exist and not resolved.is_file():
        raise ValueError("regular_file_required")
    if output and os.path.lexists(raw):
        raise ValueError("existing_output_forbidden")
    return resolved


def same_file(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except (FileNotFoundError, OSError):
        return False
