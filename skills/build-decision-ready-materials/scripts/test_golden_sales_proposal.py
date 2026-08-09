#!/usr/bin/env python3
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest

from route_material_format import route
from validate_material_plan import validate as validate_plan
from validate_reader_verdict import validate as validate_reader

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def digest(file_path: Path) -> str:
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


class SyntheticSalesGoldenTests(unittest.TestCase):
    def test_contract_route_and_blind_extraction_stay_aligned(self) -> None:
        plan = json.loads(
            (FIXTURES / "synthetic_sales_proposal_plan.json").read_text(encoding="utf-8")
        )
        verdict = json.loads(
            (FIXTURES / "synthetic_sales_reader_verdict.json").read_text(
                encoding="utf-8"
            )
        )
        forward_verdict = json.loads(
            (FIXTURES / "synthetic_sales_forward_verdict_r1.json").read_text(
                encoding="utf-8"
            )
        )
        failed_forward_verdict = json.loads(
            (FIXTURES / "synthetic_sales_forward_verdict_r0.json").read_text(
                encoding="utf-8"
            )
        )
        rendered = (FIXTURES / "synthetic_sales_proposal_render.md").read_text(
            encoding="utf-8"
        )
        rendered_r0 = (
            FIXTURES / "synthetic_sales_proposal_render_r0.md"
        ).read_text(encoding="utf-8")

        self.assertEqual(
            validate_plan(
                plan,
                completion=True,
                base_dir=FIXTURES,
                fixture_mode=True,
            ),
            [],
        )
        self.assertEqual(validate_reader(verdict), [])
        self.assertEqual(validate_reader(forward_verdict), [])
        self.assertEqual(validate_reader(failed_forward_verdict), [])
        self.assertEqual(
            route(plan["communication_job"]["use_moment"])["output_format"],
            plan["communication_job"]["output_format"],
        )

        card = plan["decision_card"]
        extraction = verdict["extraction"]
        self.assertEqual(extraction["decision"], card["decision"])
        self.assertEqual(extraction["recommendation"], card["recommendation"])
        self.assertEqual(extraction["explicit_ask"], card["explicit_ask"])
        for locator in verdict["locators"]:
            visible_heading = locator.split(":", 1)[-1].strip()
            self.assertIn(visible_heading, rendered)
        self.assertIn("estimated recovery", rendered)
        self.assertIn("Stop condition", rendered)
        self.assertEqual(forward_verdict["verdict"], "pass")
        self.assertEqual(failed_forward_verdict["verdict"], "fail")
        self.assertTrue(
            any(
                "budget amount" in issue
                for issue in failed_forward_verdict["blocking_issues"]
            )
        )
        self.assertNotIn("JPY 1.8 million", rendered_r0)
        self.assertIn("JPY 1.8 million", rendered)
        self.assertIn("1.8 million", forward_verdict["extraction"]["explicit_ask"])
        self.assertIn(
            "2026-08-15", forward_verdict["extraction"]["deadline_or_timing"]
        )
        self.assertEqual(
            plan["verification"]["final_artifact_sha256"],
            digest(FIXTURES / plan["verification"]["final_artifact_ref"]),
        )
        self.assertEqual(
            plan["verification"]["independent_reader_verdict_sha256"],
            digest(
                FIXTURES
                / plan["verification"]["independent_reader_verdict_ref"]
            ),
        )
        self.assertEqual(
            [entry["revision"] for entry in plan["verification"]["revision_history"]],
            ["r0", "r1"],
        )
        self.assertEqual(
            [entry["verdict"] for entry in plan["verification"]["revision_history"]],
            ["fail", "pass"],
        )

    def test_completion_rejects_hash_semantic_and_revision_drift(self) -> None:
        plan = json.loads(
            (FIXTURES / "synthetic_sales_proposal_plan.json").read_text(
                encoding="utf-8"
            )
        )

        broken_hash = deepcopy(plan)
        broken_hash["verification"]["final_artifact_sha256"] = "0" * 64
        errors = validate_plan(
            broken_hash,
            completion=True,
            base_dir=FIXTURES,
            fixture_mode=True,
        )
        self.assertTrue(
            any("final_artifact_sha256 does not match" in error for error in errors)
        )

        broken_semantics = deepcopy(plan)
        broken_semantics["decision_card"]["why_now"] = (
            "The immediate renewal window closes this month"
        )
        broken_semantics["verification"]["semantic_anchors"]["why_now"] = [
            "renewal window"
        ]
        errors = validate_plan(
            broken_semantics,
            completion=True,
            base_dir=FIXTURES,
            fixture_mode=True,
        )
        self.assertTrue(
            any("missing from final text extract" in error for error in errors)
        )
        self.assertTrue(
            any("missing from blind extraction" in error for error in errors)
        )

        broken_history = deepcopy(plan)
        broken_history["verification"]["revision_history"][1]["revision"] = "r2"
        broken_history["verification"]["revision_history"][1][
            "artifact_sha256"
        ] = broken_history["verification"]["revision_history"][0][
            "artifact_sha256"
        ]
        errors = validate_plan(
            broken_history,
            completion=True,
            base_dir=FIXTURES,
            fixture_mode=True,
        )
        self.assertTrue(any("revision must be r1" in error for error in errors))
        self.assertTrue(any("artifact_sha256 must be unique" in error for error in errors))

    def test_fixture_bypass_is_not_available_to_production_validation(self) -> None:
        plan = json.loads(
            (FIXTURES / "synthetic_sales_proposal_plan.json").read_text(
                encoding="utf-8"
            )
        )
        errors = validate_plan(
            plan,
            completion=True,
            base_dir=FIXTURES,
        )
        self.assertTrue(
            any("approval_evidence" in error for error in errors)
        )
        self.assertTrue(
            any("final_approval_evidence_ref" in error for error in errors)
        )
        self.assertTrue(
            any("editable_artifact_ref must end with .pptx" in error for error in errors)
        )


if __name__ == "__main__":
    unittest.main()
