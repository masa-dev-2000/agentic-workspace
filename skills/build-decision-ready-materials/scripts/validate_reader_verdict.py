#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re

EXTRACTION_FIELDS = (
    "decision",
    "recommendation",
    "causal_rationale",
    "largest_risk_or_uncertainty",
    "explicit_ask",
    "deadline_or_timing",
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def nonempty(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def valid_sha256(value) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def valid_reviewed_at(value) -> bool:
    if not nonempty(value):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def validate(
    verdict: dict,
    *,
    expected_artifact_sha256: str | None = None,
) -> list[str]:
    errors: list[str] = []
    if verdict.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if not nonempty(verdict.get("artifact_ref")):
        errors.append("artifact_ref is required")
    if not nonempty(verdict.get("reviewer_run_ref")):
        errors.append("reviewer_run_ref is required")
    if not valid_reviewed_at(verdict.get("reviewed_at")):
        errors.append("reviewed_at must be an ISO-8601 timestamp with timezone")
    reviewed_hash = verdict.get("reviewed_artifact_sha256")
    if not valid_sha256(reviewed_hash):
        errors.append("reviewed_artifact_sha256 must be a lowercase SHA-256 digest")
    elif (
        expected_artifact_sha256 is not None
        and reviewed_hash != expected_artifact_sha256
    ):
        errors.append("reviewed_artifact_sha256 does not match the final artifact")

    context = verdict.get("review_context")
    if not isinstance(context, dict):
        errors.append("review_context must be an object")
    else:
        if context.get("independent_reviewer") is not True:
            errors.append("review_context.independent_reviewer must be true")
        if context.get("received") != ["final-rendered-artifact"]:
            errors.append(
                "review_context.received must contain only final-rendered-artifact"
            )

    extraction = verdict.get("extraction")
    if not isinstance(extraction, dict):
        errors.append("extraction must be an object")
        extraction = {}
    for field in EXTRACTION_FIELDS:
        if not isinstance(extraction.get(field), str):
            errors.append(f"extraction.{field} must be a string")

    locators = verdict.get("locators")
    if not isinstance(locators, list):
        errors.append("locators must be a list")
        locators = []
    elif any(not nonempty(item) for item in locators):
        errors.append("locators entries must be non-empty strings")

    if not isinstance(verdict.get("can_act_without_presenter"), bool):
        errors.append("can_act_without_presenter must be a boolean")
    blocking = verdict.get("blocking_issues")
    if not isinstance(blocking, list) or any(not nonempty(item) for item in blocking or []):
        errors.append("blocking_issues must be a list of non-empty strings")
        blocking = []
    observations = verdict.get("nonblocking_observations")
    if not isinstance(observations, list) or any(
        not nonempty(item) for item in observations or []
    ):
        errors.append("nonblocking_observations must be a list of non-empty strings")

    outcome = verdict.get("verdict")
    if outcome not in {"pass", "fail"}:
        errors.append("verdict must be pass or fail")
    elif outcome == "pass":
        for field in EXTRACTION_FIELDS:
            if not nonempty(extraction.get(field)):
                errors.append(f"pass requires extraction.{field}")
        if not any(nonempty(item) for item in locators):
            errors.append("pass requires at least one artifact locator")
        if verdict.get("can_act_without_presenter") is not True:
            errors.append("pass requires can_act_without_presenter to be true")
        if blocking:
            errors.append("pass cannot contain blocking_issues")
    elif outcome == "fail":
        if verdict.get("can_act_without_presenter") is True and not blocking:
            errors.append(
                "fail requires can_act_without_presenter false or at least one blocking issue"
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an independent reader verdict")
    parser.add_argument(
        "--expected-artifact-sha256",
        help="optional final artifact digest that the verdict must bind",
    )
    parser.add_argument("verdict")
    args = parser.parse_args()
    try:
        payload = json.loads(Path(args.verdict).read_text(encoding="utf-8"))
    except Exception:
        print(
            json.dumps(
                {"valid": False, "errors": ["verdict must be readable UTF-8 JSON"]}
            )
        )
        return 2
    if (
        args.expected_artifact_sha256 is not None
        and not valid_sha256(args.expected_artifact_sha256)
    ):
        print(
            json.dumps(
                {
                    "valid": False,
                    "errors": [
                        "--expected-artifact-sha256 must be a lowercase SHA-256 digest"
                    ],
                }
            )
        )
        return 2
    errors = validate(
        payload,
        expected_artifact_sha256=args.expected_artifact_sha256,
    )
    print(json.dumps({"valid": not errors, "errors": errors}, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
