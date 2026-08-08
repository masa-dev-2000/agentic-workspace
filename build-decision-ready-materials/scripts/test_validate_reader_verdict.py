#!/usr/bin/env python3
from __future__ import annotations

import unittest

from validate_reader_verdict import validate


ARTIFACT_HASH = "a" * 64


def valid_verdict() -> dict:
    return {
        "schema_version": 1,
        "artifact_ref": "artifact:test-sales-proposal",
        "reviewer_run_ref": "agent-run:test-reader-001",
        "reviewed_at": "2026-07-31T10:00:00+09:00",
        "reviewed_artifact_sha256": ARTIFACT_HASH,
        "review_context": {
            "independent_reviewer": True,
            "received": ["final-rendered-artifact"],
        },
        "extraction": {
            "decision": "Approve a 90-day pilot",
            "recommendation": "Run the bounded pilot",
            "causal_rationale": "The measured saving exceeds cost with a stop condition",
            "largest_risk_or_uncertainty": "Adoption may vary by team",
            "explicit_ask": "Approve the pilot budget and owner",
            "deadline_or_timing": "By 2026-08-15",
        },
        "locators": ["slide 1: Approve a bounded 90-day pilot"],
        "can_act_without_presenter": True,
        "blocking_issues": [],
        "nonblocking_observations": [],
        "verdict": "pass",
    }


class ReaderVerdictTests(unittest.TestCase):
    def test_valid_pass(self) -> None:
        self.assertEqual(
            validate(
                valid_verdict(),
                expected_artifact_sha256=ARTIFACT_HASH,
            ),
            [],
        )

    def test_rejects_leaked_context(self) -> None:
        verdict = valid_verdict()
        verdict["review_context"]["received"].append("material-plan")
        self.assertTrue(any("received" in error for error in validate(verdict)))

    def test_rejects_inconsistent_pass(self) -> None:
        verdict = valid_verdict()
        verdict["can_act_without_presenter"] = False
        verdict["blocking_issues"] = ["The ask is unclear"]
        errors = validate(verdict)
        self.assertTrue(any("can_act_without_presenter" in error for error in errors))
        self.assertTrue(any("blocking_issues" in error for error in errors))

    def test_rejects_missing_run_timestamp_and_hash_mismatch(self) -> None:
        verdict = valid_verdict()
        verdict["reviewer_run_ref"] = ""
        verdict["reviewed_at"] = "2026-07-31"
        errors = validate(verdict, expected_artifact_sha256="b" * 64)
        self.assertTrue(any("reviewer_run_ref" in error for error in errors))
        self.assertTrue(any("reviewed_at" in error for error in errors))
        self.assertTrue(
            any("does not match the final artifact" in error for error in errors)
        )

    def test_rejects_inconsistent_fail(self) -> None:
        verdict = valid_verdict()
        verdict["verdict"] = "fail"
        errors = validate(verdict)
        self.assertTrue(any("fail requires" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
