#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

FORMATS = {"pptx", "docx", "pdf", "google-slides", "google-docs"}
ALIASES = {
    "ppt": "pptx",
    "powerpoint": "pptx",
    "slides": "pptx",
    "word": "docx",
    "document": "docx",
    "gslides": "google-slides",
    "gdocs": "google-docs",
}
DEFAULTS = {"live": "pptx", "async": "docx", "fixed-distribution": "pdf"}
BASES = {
    "live": "live-discussion",
    "async": "async-close-reading",
    "fixed-distribution": "fixed-distribution",
}


def normalize_format(value: str) -> str:
    normalized = value.strip().casefold()
    normalized = ALIASES.get(normalized, normalized)
    if normalized not in FORMATS:
        raise ValueError(f"unsupported requested format: {value}")
    return normalized


def route(use_moment: str, requested_format: str | None = None) -> dict[str, str]:
    moment = use_moment.strip().casefold()
    if moment not in DEFAULTS:
        raise ValueError(f"unsupported use moment: {use_moment}")
    if requested_format and requested_format.strip():
        return {
            "output_format": normalize_format(requested_format),
            "format_basis": "explicit-user-request",
            "use_moment": moment,
        }
    return {
        "output_format": DEFAULTS[moment],
        "format_basis": BASES[moment],
        "use_moment": moment,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Route a decision material to one format")
    parser.add_argument("--use-moment", required=True)
    parser.add_argument("--requested-format")
    args = parser.parse_args()
    try:
        result = route(args.use_moment, args.requested_format)
    except ValueError as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"valid": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
