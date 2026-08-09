#!/usr/bin/env python3
from __future__ import annotations

from copy import deepcopy
import unittest

from validate_material_plan import validate


def valid_plan() -> dict:
    return {
        "schema_version": 3,
        "decision_card": {
            "status": "approved",
            "approval_evidence": "user-message:test",
            "decision": "Approve launch",
            "recommendation": "Approve phase one",
            "why_now": "A delay loses one quarter of learning",
            "largest_uncertainty": "One segment was not tested",
            "explicit_ask": "Approve budget and owner",
            "chosen_format": "pptx",
            "recommendation_type": "bounded-pilot",
        },
        "evidence_acquisition_plan": None,
        "communication_job": {
            "purpose": "Enable a bounded launch decision",
            "decision": "Approve launch",
            "audience": ["Executive sponsor"],
            "use_moment": "live",
            "decision_owner": "COO",
            "desired_action": "Approve phase one",
            "deadline": "2026-08-15",
            "stakes": "Delayed revenue",
            "failure_cost": "One quarter delay",
            "output_format": "pptx",
            "format_basis": "live-discussion",
        },
        "claims": [
            {
                "id": "C1",
                "statement": "Pilot met the threshold",
                "classification": "fact",
                "evidence_refs": ["pilot-report:p12"],
                "method": "",
                "range_or_sensitivity": "",
                "confidence": "high",
                "counterevidence_or_gap": "One segment not tested",
            },
            {
                "id": "C2",
                "statement": "Launch phase one",
                "classification": "recommendation",
                "evidence_refs": ["C1"],
                "method": "",
                "range_or_sensitivity": "",
                "confidence": "medium",
                "counterevidence_or_gap": "Rollback required",
            },
        ],
        "narrative": {
            "governing_thought": "Launch the bounded first phase",
            "causal_chain": ["Pilot passed", "risk is bounded", "launch creates learning"],
            "recommendation": "Approve phase one",
            "primary_claim_ids": ["C2"],
            "required_messages": [
                "The pilot passed the launch threshold",
                "The first phase has a bounded rollback",
            ],
            "alternatives": [
                {"name": "Status quo", "tradeoff": "Delay revenue and learning"}
            ],
            "explicit_ask": "Approve budget and owner",
        },
        "architecture": [
            {
                "id": "U1",
                "reading_job": "Establish the choice",
                "headline": "A bounded launch captures upside while limiting risk",
                "claim_ids": ["C1", "C2"],
                "representation": "prose",
                "speaker_or_appendix_detail": "",
            }
        ],
        "human_gates": [
            {
                "id": "G1",
                "kind": "price",
                "item": "Phase-one budget",
                "status": "approved",
                "evidence_ref": "user-message:test",
            }
        ],
        "verification": {
            "content_checks": [],
            "render_checks": [],
            "editable_artifact_ref": "",
            "rendered_artifact_refs": [],
            "final_artifact_ref": "",
            "final_artifact_sha256": "",
            "text_extract_ref": "",
            "text_extract_sha256": "",
            "format_validation_refs": [],
            "human_approval_required": True,
            "independent_reader_required": True,
            "max_revision_cycles": 2,
            "independent_reader_verdict_ref": "",
            "independent_reader_verdict_sha256": "",
            "independent_reader_verdict_status": "pending",
            "final_approval_status": "pending",
            "final_approval_evidence_ref": "",
            "semantic_anchors": {
                "decision": ["launch"],
                "recommendation": ["phase one"],
                "why_now": ["one quarter"],
                "largest_uncertainty": ["one segment"],
                "explicit_ask": ["budget", "owner"],
                "required_messages": ["bounded rollback"],
            },
            "revision_history": [],
        },
    }


class MaterialPlanTests(unittest.TestCase):
    def test_valid_plan(self) -> None:
        self.assertEqual(validate(valid_plan()), [])

    def test_rejects_untraceable_estimate_and_missing_status_quo(self) -> None:
        plan = valid_plan()
        plan["claims"][0].update({
            "classification": "estimate",
            "evidence_refs": [],
            "method": "",
            "range_or_sensitivity": "",
        })
        plan["narrative"]["alternatives"] = [
            {"name": "Alternative vendor", "tradeoff": "Higher cost"}
        ]
        errors = validate(plan)
        self.assertTrue(any("evidence_refs" in error for error in errors))
        self.assertTrue(any("method" in error for error in errors))
        self.assertTrue(any("range_or_sensitivity" in error for error in errors))
        self.assertTrue(any("Status quo" in error for error in errors))

    def test_rejects_unknown_claim_and_missing_human_gate(self) -> None:
        plan = valid_plan()
        plan["architecture"][0]["claim_ids"] = ["C404"]
        plan["verification"]["human_approval_required"] = False
        errors = validate(plan)
        self.assertTrue(any("unknown claim" in error for error in errors))
        self.assertTrue(any("human_approval_required" in error for error in errors))

    def test_completion_rejects_pending_consequential_gate(self) -> None:
        plan = valid_plan()
        plan["human_gates"][0].update(
            {
                "item": "Final offer price",
                "status": "pending",
                "evidence_ref": "",
            }
        )
        self.assertFalse(any("cannot be pending" in error for error in validate(plan)))
        self.assertTrue(
            any("cannot be pending" in error for error in validate(plan, completion=True))
        )

    def test_rejects_decision_card_drift_and_wrong_auto_route(self) -> None:
        plan = valid_plan()
        plan["decision_card"]["recommendation"] = "Wait"
        plan["communication_job"]["output_format"] = "docx"
        errors = validate(plan)
        self.assertTrue(any("decision_card.recommendation" in error for error in errors))
        self.assertTrue(any("automatic routing" in error for error in errors))

    def test_completion_rejects_missing_artifact_and_blind_reader_evidence(self) -> None:
        errors = validate(valid_plan(), completion=True)
        self.assertTrue(any("base_dir" in error for error in errors))
        self.assertTrue(any("editable_artifact_ref" in error for error in errors))
        self.assertTrue(
            any("independent_reader_verdict_status" in error for error in errors)
        )
        self.assertTrue(any("final_approval_status" in error for error in errors))

    def test_rejects_recommendation_without_resolved_verified_support(self) -> None:
        plan = valid_plan()
        plan["claims"][1]["evidence_refs"] = []
        self.assertTrue(
            any(
                "supporting claim IDs" in error
                for error in validate(plan)
            )
        )

        plan = valid_plan()
        plan["claims"][1]["evidence_refs"] = ["C404"]
        self.assertTrue(
            any("unknown claim" in error for error in validate(plan))
        )

        plan = valid_plan()
        plan["claims"][0]["classification"] = "unverified"
        plan["claims"][0]["evidence_refs"] = []
        self.assertTrue(
            any("cannot rely on unverified" in error for error in validate(plan))
        )

    def test_rejects_unverified_primary_claim(self) -> None:
        plan = valid_plan()
        plan["claims"][1]["classification"] = "unverified"
        errors = validate(plan)
        self.assertTrue(any("cannot use unverified" in error for error in errors))

    def test_explicit_format_requires_request_evidence(self) -> None:
        plan = valid_plan()
        plan["communication_job"].update(
            {
                "output_format": "docx",
                "format_basis": "explicit-user-request",
                "format_request_evidence_ref": "",
            }
        )
        plan["decision_card"]["chosen_format"] = "docx"
        self.assertTrue(
            any("format_request_evidence_ref" in error for error in validate(plan))
        )
        plan["communication_job"][
            "format_request_evidence_ref"
        ] = "user-message:explicit-docx"
        self.assertEqual(validate(plan), [])

    def test_price_signal_requires_price_gate(self) -> None:
        plan = valid_plan()
        plan["human_gates"] = []
        self.assertTrue(
            any("requires a price gate" in error for error in validate(plan))
        )

    def test_evidence_acquisition_requires_executable_plan(self) -> None:
        plan = valid_plan()
        plan["decision_card"]["recommendation_type"] = "evidence-acquisition"
        errors = validate(plan)
        self.assertTrue(any("evidence_acquisition_plan is required" in error for error in errors))

        plan["evidence_acquisition_plan"] = {
            "status": "proposed",
            "owner_role": "",
            "budget_range": {},
            "start_by": "",
            "return_decision_by": "",
            "milestones": [],
            "success_criteria": [],
            "stop_conditions": [],
            "dependencies": [],
            "final_decision_owner": "",
            "approval_gate_ids": [],
        }
        errors = validate(plan)
        for required in (
            "owner_role",
            "amount_or_range",
            "proposal_basis",
            "assumptions",
            "range_or_sensitivity",
            "start_by",
            "return_decision_by",
            "milestones",
            "success_criteria",
            "stop_conditions",
            "dependencies",
            "final_decision_owner",
            "approval_gate_ids",
        ):
            self.assertTrue(any(required in error for error in errors), required)

    def test_valid_proposed_evidence_acquisition_plan(self) -> None:
        plan = valid_plan()
        plan["decision_card"]["recommendation_type"] = "evidence-acquisition"
        plan["evidence_acquisition_plan"] = {
            "status": "proposed",
            "owner_role": "Strategy lead",
            "budget_range": {
                "amount_or_range": "JPY 1.0M to 2.0M proposed ceiling",
                "proposal_basis": "Four work packages using stated planning rates",
                "assumptions": [
                    "Regulatory and customer work can proceed in parallel",
                    "No production integration is included",
                ],
                "range_or_sensitivity": "Counsel effort is the dominant sensitivity",
                "approval_gate_id": "G1",
            },
            "start_by": "Within five business days of approval",
            "return_decision_by": "2026-09-01",
            "milestones": [
                {
                    "id": "M1",
                    "outcome": "Authoritative regulatory classification",
                    "owner_role": "Regulatory lead",
                    "due_by": "2026-08-15",
                    "required_evidence": "Written counsel opinion",
                }
            ],
            "success_criteria": ["Regulatory path and full cost are decision-ready"],
            "stop_conditions": ["Stop if the regulatory path exceeds the approved horizon"],
            "dependencies": ["Access to qualified regulatory counsel"],
            "final_decision_owner": "CEO",
            "approval_gate_ids": ["G1"],
        }
        self.assertEqual(validate(plan), [])
        errors = validate(plan, completion=True)
        self.assertTrue(
            any("status must be approved at completion" in error for error in errors)
        )

    def test_approved_evidence_plan_requires_approved_linked_gates(self) -> None:
        plan = valid_plan()
        plan["decision_card"]["recommendation_type"] = "evidence-acquisition"
        plan["evidence_acquisition_plan"] = {
            "status": "approved",
            "owner_role": "Strategy lead",
            "budget_range": {
                "amount_or_range": "JPY 1.0M to 2.0M",
                "proposal_basis": "Scoped work-package estimate",
                "assumptions": ["No production work"],
                "range_or_sensitivity": "Counsel effort",
                "approval_gate_id": "G1",
            },
            "start_by": "2026-08-01",
            "return_decision_by": "2026-09-01",
            "milestones": [
                {
                    "id": "M1",
                    "outcome": "Evidence package",
                    "owner_role": "Strategy lead",
                    "due_by": "2026-08-20",
                    "required_evidence": "Signed evidence checklist",
                }
            ],
            "success_criteria": ["Decision inputs are complete"],
            "stop_conditions": ["Stop on regulatory infeasibility"],
            "dependencies": ["Counsel access"],
            "final_decision_owner": "CEO",
            "approval_gate_ids": ["G1"],
        }
        plan["human_gates"][0]["status"] = "pending"
        plan["human_gates"][0]["evidence_ref"] = ""
        self.assertTrue(
            any("all linked human gates" in error for error in validate(plan))
        )

    def test_non_evidence_recommendation_rejects_evidence_plan(self) -> None:
        plan = valid_plan()
        plan["evidence_acquisition_plan"] = {"status": "proposed"}
        self.assertTrue(
            any("must be null" in error for error in validate(plan))
        )

    def test_proposed_decision_card_is_valid_only_as_draft(self) -> None:
        plan = valid_plan()
        plan["decision_card"]["status"] = "proposed"
        plan["decision_card"]["approval_evidence"] = ""
        self.assertEqual(validate(plan), [])
        self.assertTrue(
            any(
                "decision_card.status must be approved at completion" in error
                for error in validate(plan, completion=True)
            )
        )

    def test_approved_decision_card_requires_human_evidence(self) -> None:
        plan = valid_plan()
        plan["decision_card"]["approval_evidence"] = ""
        self.assertTrue(
            any(
                "approval_evidence is required when approved" in error
                for error in validate(plan)
            )
        )

    def test_archived_v2_requires_explicit_legacy_mode(self) -> None:
        legacy = deepcopy(valid_plan())
        legacy["schema_version"] = 2
        legacy["decision_card"].pop("recommendation_type")
        legacy.pop("evidence_acquisition_plan")
        self.assertTrue(any("schema_version must be 3" in error for error in validate(legacy)))
        self.assertEqual(validate(legacy, legacy_v2=True), [])
        self.assertTrue(
            any(
                "cannot be used for completion" in error
                for error in validate(legacy, legacy_v2=True, completion=True)
            )
        )


if __name__ == "__main__":
    unittest.main()
