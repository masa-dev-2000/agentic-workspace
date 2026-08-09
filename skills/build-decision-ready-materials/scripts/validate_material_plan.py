#!/usr/bin/env python3
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import unicodedata

from validate_reader_verdict import validate as validate_reader_verdict
from validate_reader_verdict import valid_sha256

CLASSIFICATIONS = {"fact", "estimate", "hypothesis", "recommendation", "unverified"}
CONFIDENCE = {"high", "medium", "low"}
FORMATS = {"pptx", "docx", "pdf", "google-slides", "google-docs"}
REPRESENTATIONS = {
    "prose",
    "bullets",
    "table",
    "chart",
    "image",
    "diagram",
    "timeline",
}
USE_MOMENTS = {"live", "async", "fixed-distribution"}
FORMAT_BASES = {
    "explicit-user-request",
    "live-discussion",
    "async-close-reading",
    "fixed-distribution",
}
DEFAULT_FORMATS = {"live": "pptx", "async": "docx", "fixed-distribution": "pdf"}
DEFAULT_BASES = {
    "live": "live-discussion",
    "async": "async-close-reading",
    "fixed-distribution": "fixed-distribution",
}
EDITABLE_SUFFIXES = {
    "pptx": {".pptx"},
    "docx": {".docx"},
    "pdf": {".pdf"},
    "google-slides": {".pptx"},
    "google-docs": {".docx"},
}
GATE_KINDS = {"price", "legal", "external-claim", "production", "other"}
GATE_STATUSES = {"approved", "not-applicable", "pending"}
RECOMMENDATION_TYPES = {
    "commit",
    "bounded-pilot",
    "evidence-acquisition",
    "hold",
    "reject",
    "other",
}
EVIDENCE_PLAN_STATUSES = {"proposed", "approved"}
PRODUCTION_EVIDENCE_PREFIXES = (
    "user-message:",
    "approval-record:",
    "human-approval:",
)
FIXTURE_EVIDENCE_PREFIXES = ("fixture:", "synthetic-fixture:")
PRICE_SIGNAL_RE = re.compile(
    r"(?:"
    r"\b(?:budget|price|pricing|fee|fees|spend|spending|"
    r"jpy|usd|eur|gbp|cad|aud)\b|"
    r"[¥$€£]|予算|価格|費用|料金|(?:^|[^\w])円(?:[^\w]|$)|万円"
    r")",
    re.IGNORECASE,
)
ANCHOR_SOURCE_FIELDS = {
    "decision": "decision",
    "recommendation": "recommendation",
    "why_now": "why_now",
    "largest_uncertainty": "largest_uncertainty",
    "explicit_ask": "explicit_ask",
}
ANCHOR_EXTRACTION_FIELDS = {
    "decision": ("decision",),
    "recommendation": ("recommendation",),
    "why_now": ("causal_rationale",),
    "largest_uncertainty": ("largest_risk_or_uncertainty",),
    "explicit_ask": ("explicit_ask", "deadline_or_timing"),
    "required_messages": (
        "decision",
        "recommendation",
        "causal_rationale",
        "largest_risk_or_uncertainty",
        "explicit_ask",
        "deadline_or_timing",
    ),
}


def nonempty(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def normalized_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.sub(r"[^\w]+", " ", value).split())


def contains_anchor(haystack: str, anchor: str) -> bool:
    normalized_anchor = normalized_text(anchor)
    return bool(normalized_anchor) and normalized_anchor in normalized_text(haystack)


def evidence_ref_allowed(value, *, fixture_mode: bool) -> bool:
    if not nonempty(value):
        return False
    prefixes = PRODUCTION_EVIDENCE_PREFIXES
    if fixture_mode:
        prefixes += FIXTURE_EVIDENCE_PREFIXES
    return value.startswith(prefixes)


def iter_text(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from iter_text(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_text(item)


def has_price_signal(plan: dict) -> bool:
    scoped = {
        key: plan.get(key)
        for key in (
            "decision_card",
            "communication_job",
            "claims",
            "narrative",
            "architecture",
            "evidence_acquisition_plan",
        )
    }
    return any(PRICE_SIGNAL_RE.search(text) for text in iter_text(scoped))


def migrate_legacy_v2(plan: dict) -> dict:
    migrated = deepcopy(plan)
    migrated["schema_version"] = 3
    card = migrated.setdefault("decision_card", {})
    card["recommendation_type"] = "other"
    migrated["evidence_acquisition_plan"] = None

    job = migrated.setdefault("communication_job", {})
    if (
        job.get("format_basis") == "explicit-user-request"
        and not nonempty(job.get("format_request_evidence_ref"))
    ):
        job["format_request_evidence_ref"] = card.get("approval_evidence", "")

    claims = migrated.get("claims", [])
    recommendation_ids = [
        claim.get("id")
        for claim in claims
        if isinstance(claim, dict)
        and claim.get("classification") == "recommendation"
        and nonempty(claim.get("id"))
    ]
    narrative = migrated.setdefault("narrative", {})
    narrative.setdefault("primary_claim_ids", recommendation_ids[:1])

    verification = migrated.setdefault("verification", {})
    verification.setdefault("final_artifact_ref", "")
    verification.setdefault("final_artifact_sha256", "")
    verification.setdefault("text_extract_ref", "")
    verification.setdefault("text_extract_sha256", "")
    verification.setdefault("independent_reader_verdict_sha256", "")
    verification.setdefault("final_approval_evidence_ref", "")
    verification.setdefault("revision_history", [])
    verification.setdefault(
        "semantic_anchors",
        {
            "decision": [card.get("decision", "")],
            "recommendation": [card.get("recommendation", "")],
            "why_now": [card.get("why_now", "")],
            "largest_uncertainty": [card.get("largest_uncertainty", "")],
            "explicit_ask": [card.get("explicit_ask", "")],
            "required_messages": list(narrative.get("required_messages", [])),
        },
    )
    return migrated


def sha256_file(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_ref(value: str) -> str:
    return value.replace("\\", "/").removeprefix("./")


def resolve_task_file(
    reference,
    *,
    base_dir: Path,
    field: str,
    errors: list[str],
) -> Path | None:
    if not nonempty(reference):
        errors.append(f"{field} is required at completion")
        return None
    if Path(reference).is_absolute() or PureWindowsPath(reference).is_absolute():
        errors.append(f"{field} must be task-relative")
        return None
    if ".." in PureWindowsPath(reference).parts or ".." in Path(reference).parts:
        errors.append(f"{field} must not escape the task directory")
        return None
    root = base_dir.resolve()
    candidate = (root / reference).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        errors.append(f"{field} must resolve inside the task directory")
        return None
    if not candidate.is_file():
        errors.append(f"{field} does not resolve to a file")
        return None
    try:
        if candidate.stat().st_size <= 0:
            errors.append(f"{field} must resolve to a non-empty file")
            return None
    except OSError:
        errors.append(f"{field} cannot be inspected")
        return None
    return candidate


def read_json_file(file_path: Path, *, field: str, errors: list[str]) -> dict | None:
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        errors.append(f"{field} must be readable UTF-8 JSON")
        return None
    if not isinstance(payload, dict):
        errors.append(f"{field} must contain a JSON object")
        return None
    return payload


def validate_semantic_anchors(
    verification: dict,
    *,
    card: dict,
    narrative: dict,
    errors: list[str],
) -> dict[str, list[str]]:
    anchors = verification.get("semantic_anchors")
    if not isinstance(anchors, dict):
        errors.append("verification.semantic_anchors must be an object")
        return {}
    expected_keys = set(ANCHOR_SOURCE_FIELDS) | {"required_messages"}
    unknown = set(anchors) - expected_keys
    if unknown:
        errors.append(
            "verification.semantic_anchors contains unsupported keys: "
            + ", ".join(sorted(unknown))
        )
    result: dict[str, list[str]] = {}
    for key in sorted(expected_keys):
        values = anchors.get(key)
        if (
            not isinstance(values, list)
            or not values
            or any(not nonempty(item) for item in values)
        ):
            errors.append(
                f"verification.semantic_anchors.{key} requires non-empty strings"
            )
            continue
        result[key] = values
        if key == "required_messages":
            source = "\n".join(
                item
                for item in narrative.get("required_messages", [])
                if nonempty(item)
            )
        else:
            source = str(card.get(ANCHOR_SOURCE_FIELDS[key], ""))
        for anchor in values:
            if not contains_anchor(source, anchor):
                errors.append(
                    f"verification.semantic_anchors.{key} anchor {anchor!r} "
                    "must come from the approved plan meaning"
                )
    return result


def validate_anchor_presence(
    anchors: dict[str, list[str]],
    *,
    final_text: str,
    extraction: dict,
    errors: list[str],
) -> None:
    for key, values in anchors.items():
        extraction_text = "\n".join(
            str(extraction.get(field, ""))
            for field in ANCHOR_EXTRACTION_FIELDS[key]
        )
        for anchor in values:
            if not contains_anchor(final_text, anchor):
                errors.append(
                    f"semantic anchor {key}:{anchor!r} is missing from final text extract"
                )
            if not contains_anchor(extraction_text, anchor):
                errors.append(
                    f"semantic anchor {key}:{anchor!r} is missing from blind extraction"
                )


def validate_format_receipt(
    receipt_path: Path,
    *,
    final_artifact_sha256: str,
    field: str,
    errors: list[str],
) -> None:
    receipt = read_json_file(receipt_path, field=field, errors=errors)
    if receipt is None:
        return
    if receipt.get("schema_version") != 1:
        errors.append(f"{field}.schema_version must be 1")
    if receipt.get("status") != "pass":
        errors.append(f"{field}.status must be pass")
    if receipt.get("artifact_sha256") != final_artifact_sha256:
        errors.append(f"{field}.artifact_sha256 must match the final artifact")
    checks = receipt.get("checks")
    if (
        not isinstance(checks, list)
        or not checks
        or any(not nonempty(item) for item in checks)
    ):
        errors.append(f"{field}.checks requires non-empty check evidence")


def validate_revision_history(
    history,
    *,
    max_revision_cycles: int,
    base_dir: Path,
    final_artifact_ref: str,
    final_artifact_sha256: str,
    final_verdict_ref: str,
    final_verdict_sha256: str,
    errors: list[str],
) -> None:
    if not isinstance(history, list) or not history:
        errors.append("verification.revision_history requires at least r0 at completion")
        return
    if len(history) > max_revision_cycles + 1:
        errors.append(
            "verification.revision_history exceeds the initial artifact plus two changes"
        )

    artifact_hashes: set[str] = set()
    verdict_hashes: set[str] = set()
    reviewer_runs: set[str] = set()
    reviewed_times: list[datetime] = []

    for index, entry in enumerate(history):
        field = f"verification.revision_history[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{field} must be an object")
            continue
        expected_revision = f"r{index}"
        if entry.get("revision") != expected_revision:
            errors.append(f"{field}.revision must be {expected_revision}")

        artifact_ref = entry.get("artifact_ref")
        artifact_hash = entry.get("artifact_sha256")
        verdict_ref = entry.get("reader_verdict_ref")
        verdict_hash = entry.get("reader_verdict_sha256")
        outcome = entry.get("verdict")

        if not valid_sha256(artifact_hash):
            errors.append(f"{field}.artifact_sha256 must be a lowercase SHA-256 digest")
        elif artifact_hash in artifact_hashes:
            errors.append(f"{field}.artifact_sha256 must be unique")
        else:
            artifact_hashes.add(artifact_hash)
        if not valid_sha256(verdict_hash):
            errors.append(
                f"{field}.reader_verdict_sha256 must be a lowercase SHA-256 digest"
            )
        elif verdict_hash in verdict_hashes:
            errors.append(f"{field}.reader_verdict_sha256 must be unique")
        else:
            verdict_hashes.add(verdict_hash)
        if outcome not in {"pass", "fail"}:
            errors.append(f"{field}.verdict must be pass or fail")
        elif index < len(history) - 1 and outcome != "fail":
            errors.append(f"{field}.verdict must be fail before the final revision")
        elif index == len(history) - 1 and outcome != "pass":
            errors.append(f"{field}.verdict must be pass for the final revision")

        artifact_path = resolve_task_file(
            artifact_ref,
            base_dir=base_dir,
            field=f"{field}.artifact_ref",
            errors=errors,
        )
        verdict_path = resolve_task_file(
            verdict_ref,
            base_dir=base_dir,
            field=f"{field}.reader_verdict_ref",
            errors=errors,
        )
        actual_artifact_hash = None
        if artifact_path is not None:
            actual_artifact_hash = sha256_file(artifact_path)
            if valid_sha256(artifact_hash) and actual_artifact_hash != artifact_hash:
                errors.append(f"{field}.artifact_sha256 does not match its file")
        if verdict_path is not None:
            actual_verdict_hash = sha256_file(verdict_path)
            if valid_sha256(verdict_hash) and actual_verdict_hash != verdict_hash:
                errors.append(f"{field}.reader_verdict_sha256 does not match its file")
            verdict = read_json_file(
                verdict_path,
                field=f"{field}.reader_verdict_ref",
                errors=errors,
            )
            if verdict is not None:
                errors.extend(
                    f"{field}.reader_verdict: {error}"
                    for error in validate_reader_verdict(
                        verdict,
                        expected_artifact_sha256=actual_artifact_hash,
                    )
                )
                if normalized_ref(str(verdict.get("artifact_ref", ""))) != normalized_ref(
                    str(artifact_ref or "")
                ):
                    errors.append(
                        f"{field}.reader_verdict artifact_ref does not match artifact_ref"
                    )
                if outcome in {"pass", "fail"} and verdict.get("verdict") != outcome:
                    errors.append(
                        f"{field}.verdict does not match the reader verdict payload"
                    )
                reviewer_run = verdict.get("reviewer_run_ref")
                if nonempty(reviewer_run):
                    if reviewer_run in reviewer_runs:
                        errors.append(
                            f"{field}.reader_verdict reviewer_run_ref must be unique"
                        )
                    reviewer_runs.add(reviewer_run)
                reviewed_at = verdict.get("reviewed_at")
                if nonempty(reviewed_at):
                    try:
                        reviewed_times.append(
                            datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
                        )
                    except ValueError:
                        pass

    if len(reviewed_times) == len(history) and any(
        current <= previous
        for previous, current in zip(reviewed_times, reviewed_times[1:])
    ):
        errors.append(
            "verification.revision_history reviewed_at timestamps must increase"
        )

    final_entry = history[-1] if history and isinstance(history[-1], dict) else {}
    if normalized_ref(str(final_entry.get("artifact_ref", ""))) != normalized_ref(
        final_artifact_ref
    ):
        errors.append(
            "verification.revision_history final artifact_ref must match final_artifact_ref"
        )
    if final_entry.get("artifact_sha256") != final_artifact_sha256:
        errors.append(
            "verification.revision_history final artifact_sha256 must match "
            "final_artifact_sha256"
        )
    if normalized_ref(str(final_entry.get("reader_verdict_ref", ""))) != normalized_ref(
        final_verdict_ref
    ):
        errors.append(
            "verification.revision_history final reader_verdict_ref must match "
            "independent_reader_verdict_ref"
        )
    if final_entry.get("reader_verdict_sha256") != final_verdict_sha256:
        errors.append(
            "verification.revision_history final reader_verdict_sha256 must match "
            "independent_reader_verdict_sha256"
        )


def validate(
    plan: dict,
    *,
    completion: bool = False,
    base_dir: str | Path | None = None,
    fixture_mode: bool = False,
    legacy_v2: bool = False,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(plan, dict):
        return ["plan must be a JSON object"]
    if plan.get("schema_version") == 2 and legacy_v2:
        if completion:
            return ["legacy v2 validation cannot be used for completion"]
        plan = migrate_legacy_v2(plan)
    elif plan.get("schema_version") != 3:
        errors.append("schema_version must be 3")

    card = plan.get("decision_card")
    if not isinstance(card, dict):
        errors.append("decision_card must be an object")
        card = {}
    card_status = card.get("status")
    if card_status not in {"proposed", "approved"}:
        errors.append("decision_card.status must be proposed or approved")
    if completion and card_status != "approved":
        errors.append("decision_card.status must be approved at completion")
    for field in (
        "decision",
        "recommendation",
        "why_now",
        "largest_uncertainty",
        "explicit_ask",
    ):
        if not nonempty(card.get(field)):
            errors.append(f"decision_card.{field} is required")
    approval_evidence = card.get("approval_evidence")
    if card_status == "approved" and not nonempty(approval_evidence):
        errors.append("decision_card.approval_evidence is required when approved")
    if nonempty(approval_evidence) and not evidence_ref_allowed(
        approval_evidence, fixture_mode=fixture_mode
    ):
        errors.append(
            "decision_card.approval_evidence must reference explicit human approval"
        )
    if card.get("chosen_format") not in FORMATS:
        errors.append("decision_card.chosen_format is invalid")
    recommendation_type = card.get("recommendation_type")
    if recommendation_type not in RECOMMENDATION_TYPES:
        errors.append("decision_card.recommendation_type is invalid")

    evidence_plan = plan.get("evidence_acquisition_plan")
    evidence_plan_gate_ids: list[str] = []
    evidence_plan_status = None
    evidence_budget_gate_id = None
    if recommendation_type == "evidence-acquisition":
        if not isinstance(evidence_plan, dict):
            errors.append(
                "evidence_acquisition_plan is required for evidence-acquisition"
            )
            evidence_plan = {}
        evidence_plan_status = evidence_plan.get("status")
        if evidence_plan_status not in EVIDENCE_PLAN_STATUSES:
            errors.append("evidence_acquisition_plan.status is invalid")
        for item_field in (
            "owner_role",
            "start_by",
            "return_decision_by",
            "final_decision_owner",
        ):
            if not nonempty(evidence_plan.get(item_field)):
                errors.append(
                    f"evidence_acquisition_plan.{item_field} is required"
                )

        budget = evidence_plan.get("budget_range")
        if not isinstance(budget, dict):
            errors.append("evidence_acquisition_plan.budget_range must be an object")
            budget = {}
        for item_field in (
            "amount_or_range",
            "proposal_basis",
            "range_or_sensitivity",
            "approval_gate_id",
        ):
            if not nonempty(budget.get(item_field)):
                errors.append(
                    f"evidence_acquisition_plan.budget_range.{item_field} is required"
                )
        assumptions = budget.get("assumptions")
        if (
            not isinstance(assumptions, list)
            or not assumptions
            or any(not nonempty(item) for item in assumptions)
        ):
            errors.append(
                "evidence_acquisition_plan.budget_range.assumptions "
                "requires non-empty strings"
            )
        evidence_budget_gate_id = budget.get("approval_gate_id")

        milestones = evidence_plan.get("milestones")
        if not isinstance(milestones, list) or not milestones:
            errors.append(
                "evidence_acquisition_plan.milestones requires at least one milestone"
            )
            milestones = []
        milestone_ids: set[str] = set()
        for index, milestone in enumerate(milestones):
            field = f"evidence_acquisition_plan.milestones[{index}]"
            if not isinstance(milestone, dict):
                errors.append(f"{field} must be an object")
                continue
            milestone_id = milestone.get("id")
            if not nonempty(milestone_id):
                errors.append(f"{field}.id is required")
            elif milestone_id in milestone_ids:
                errors.append(f"{field}.id must be unique")
            else:
                milestone_ids.add(milestone_id)
            for item_field in (
                "outcome",
                "owner_role",
                "due_by",
                "required_evidence",
            ):
                if not nonempty(milestone.get(item_field)):
                    errors.append(f"{field}.{item_field} is required")

        for item_field in (
            "success_criteria",
            "stop_conditions",
            "dependencies",
            "approval_gate_ids",
        ):
            values = evidence_plan.get(item_field)
            if (
                not isinstance(values, list)
                or not values
                or any(not nonempty(item) for item in values)
            ):
                errors.append(
                    f"evidence_acquisition_plan.{item_field} "
                    "requires non-empty strings"
                )
        if isinstance(evidence_plan.get("approval_gate_ids"), list):
            evidence_plan_gate_ids = evidence_plan["approval_gate_ids"]
        if completion and evidence_plan_status != "approved":
            errors.append(
                "evidence_acquisition_plan.status must be approved at completion"
            )
    elif evidence_plan is not None:
        errors.append(
            "evidence_acquisition_plan must be null unless recommendation_type "
            "is evidence-acquisition"
        )

    job = plan.get("communication_job")
    if not isinstance(job, dict):
        errors.append("communication_job must be an object")
        job = {}
    for field in (
        "purpose",
        "decision",
        "decision_owner",
        "desired_action",
        "deadline",
        "stakes",
        "failure_cost",
    ):
        if not nonempty(job.get(field)):
            errors.append(f"communication_job.{field} is required")
    if not isinstance(job.get("audience"), list) or not any(
        nonempty(item) for item in job.get("audience", [])
    ):
        errors.append("communication_job.audience requires at least one reader")
    if job.get("output_format") not in FORMATS:
        errors.append("communication_job.output_format is invalid")
    use_moment = job.get("use_moment")
    if use_moment not in USE_MOMENTS:
        errors.append("communication_job.use_moment is invalid")
    format_basis = job.get("format_basis")
    if format_basis not in FORMAT_BASES:
        errors.append("communication_job.format_basis is invalid")
    elif use_moment in USE_MOMENTS and format_basis != "explicit-user-request":
        if format_basis != DEFAULT_BASES[use_moment]:
            errors.append("communication_job.format_basis does not match use_moment")
        if job.get("output_format") != DEFAULT_FORMATS[use_moment]:
            errors.append("communication_job.output_format does not match automatic routing")
    elif format_basis == "explicit-user-request" and not evidence_ref_allowed(
        job.get("format_request_evidence_ref"),
        fixture_mode=fixture_mode,
    ):
        errors.append(
            "communication_job.format_request_evidence_ref is required for "
            "explicit-user-request"
        )

    claims = plan.get("claims")
    if not isinstance(claims, list) or not claims:
        errors.append("claims requires at least one claim")
        claims = []
    claim_ids: set[str] = set()
    claim_by_id: dict[str, dict] = {}
    for index, claim in enumerate(claims):
        field = f"claims[{index}]"
        if not isinstance(claim, dict):
            errors.append(f"{field} must be an object")
            continue
        claim_id = claim.get("id")
        if not nonempty(claim_id):
            errors.append(f"{field}.id is required")
        elif claim_id in claim_ids:
            errors.append(f"{field}.id must be unique")
        else:
            claim_ids.add(claim_id)
            claim_by_id[claim_id] = claim
        if not nonempty(claim.get("statement")):
            errors.append(f"{field}.statement is required")
        classification = claim.get("classification")
        if classification not in CLASSIFICATIONS:
            errors.append(f"{field}.classification is invalid")
        evidence = claim.get("evidence_refs")
        if classification in {"fact", "estimate"} and (
            not isinstance(evidence, list)
            or not any(nonempty(item) for item in evidence)
        ):
            errors.append(f"{field}.evidence_refs required for {classification}")
        if classification == "estimate":
            if not nonempty(claim.get("method")):
                errors.append(f"{field}.method required for estimate")
            if not nonempty(claim.get("range_or_sensitivity")):
                errors.append(f"{field}.range_or_sensitivity required for estimate")
        if classification == "hypothesis" and not nonempty(
            claim.get("counterevidence_or_gap")
        ):
            errors.append(f"{field}.counterevidence_or_gap required for hypothesis")
        if claim.get("confidence") not in CONFIDENCE:
            errors.append(f"{field}.confidence is invalid")

    recommendation_ids: set[str] = set()
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict) or claim.get("classification") != "recommendation":
            continue
        field = f"claims[{index}]"
        claim_id = claim.get("id")
        if nonempty(claim_id):
            recommendation_ids.add(claim_id)
        evidence = claim.get("evidence_refs")
        if (
            not isinstance(evidence, list)
            or not evidence
            or any(not nonempty(item) for item in evidence)
        ):
            errors.append(
                f"{field}.evidence_refs must resolve to supporting claim IDs"
            )
            continue
        evidentiary_support = False
        for reference in evidence:
            supporting = claim_by_id.get(reference)
            if supporting is None:
                errors.append(
                    f"{field}.evidence_refs references unknown claim {reference!r}"
                )
                continue
            if reference == claim_id:
                errors.append(f"{field}.evidence_refs cannot reference itself")
                continue
            supporting_class = supporting.get("classification")
            if supporting_class == "unverified":
                errors.append(
                    f"{field}.evidence_refs cannot rely on unverified claim {reference!r}"
                )
                continue
            if supporting_class in {"fact", "estimate", "hypothesis"}:
                evidentiary_support = True
        if not evidentiary_support:
            errors.append(
                f"{field}.evidence_refs requires a fact, estimate, or hypothesis"
            )

    narrative = plan.get("narrative")
    if not isinstance(narrative, dict):
        errors.append("narrative must be an object")
        narrative = {}
    for field in ("governing_thought", "recommendation", "explicit_ask"):
        if not nonempty(narrative.get(field)):
            errors.append(f"narrative.{field} is required")
    required_messages = narrative.get("required_messages")
    if not isinstance(required_messages, list) or not any(
        nonempty(item) for item in required_messages or []
    ):
        errors.append("narrative.required_messages requires at least one message")
    if not isinstance(narrative.get("causal_chain"), list) or len(
        [item for item in narrative.get("causal_chain", []) if nonempty(item)]
    ) < 2:
        errors.append("narrative.causal_chain requires at least two links")
    alternatives = narrative.get("alternatives")
    if not isinstance(alternatives, list) or not alternatives:
        errors.append("narrative.alternatives requires the status quo")
    elif not any(
        isinstance(item, dict)
        and "status quo" in str(item.get("name", "")).casefold()
        and nonempty(item.get("tradeoff"))
        for item in alternatives
    ):
        errors.append("narrative.alternatives must include Status quo with a tradeoff")
    primary_claim_ids = narrative.get("primary_claim_ids")
    if not isinstance(primary_claim_ids, list) or not primary_claim_ids:
        errors.append("narrative.primary_claim_ids requires at least one claim")
        primary_claim_ids = []
    else:
        for claim_id in primary_claim_ids:
            primary = claim_by_id.get(claim_id)
            if primary is None:
                errors.append(
                    f"narrative.primary_claim_ids references unknown claim {claim_id!r}"
                )
            elif primary.get("classification") == "unverified":
                errors.append(
                    f"narrative.primary_claim_ids cannot use unverified claim {claim_id!r}"
                )
    if recommendation_ids and not recommendation_ids.intersection(primary_claim_ids):
        errors.append(
            "narrative.primary_claim_ids must include a recommendation claim"
        )

    architecture = plan.get("architecture")
    if not isinstance(architecture, list) or not architecture:
        errors.append("architecture requires at least one unit")
        architecture = []
    unit_ids: set[str] = set()
    used_claim_ids: set[str] = set()
    for index, unit in enumerate(architecture):
        field = f"architecture[{index}]"
        if not isinstance(unit, dict):
            errors.append(f"{field} must be an object")
            continue
        unit_id = unit.get("id")
        if not nonempty(unit_id):
            errors.append(f"{field}.id is required")
        elif unit_id in unit_ids:
            errors.append(f"{field}.id must be unique")
        else:
            unit_ids.add(unit_id)
        for item_field in ("reading_job", "headline"):
            if not nonempty(unit.get(item_field)):
                errors.append(f"{field}.{item_field} is required")
        if unit.get("representation") not in REPRESENTATIONS:
            errors.append(f"{field}.representation is invalid")
        links = unit.get("claim_ids")
        if not isinstance(links, list) or not links:
            errors.append(f"{field}.claim_ids requires at least one claim")
        else:
            for claim_id in links:
                if claim_id not in claim_ids:
                    errors.append(
                        f"{field}.claim_ids references unknown claim {claim_id!r}"
                    )
                else:
                    used_claim_ids.add(claim_id)
    for claim_id in primary_claim_ids:
        if claim_id in claim_ids and claim_id not in used_claim_ids:
            errors.append(
                f"primary claim {claim_id!r} must appear in architecture"
            )

    if nonempty(card.get("decision")) and nonempty(job.get("decision")):
        if card["decision"].strip() != job["decision"].strip():
            errors.append("decision_card.decision must match communication_job.decision")
    if nonempty(card.get("recommendation")) and nonempty(
        narrative.get("recommendation")
    ):
        if card["recommendation"].strip() != narrative["recommendation"].strip():
            errors.append(
                "decision_card.recommendation must match narrative.recommendation"
            )
    if nonempty(card.get("explicit_ask")) and nonempty(narrative.get("explicit_ask")):
        if card["explicit_ask"].strip() != narrative["explicit_ask"].strip():
            errors.append("decision_card.explicit_ask must match narrative.explicit_ask")
    if card.get("chosen_format") in FORMATS and job.get("output_format") in FORMATS:
        if card["chosen_format"] != job["output_format"]:
            errors.append(
                "decision_card.chosen_format must match communication_job.output_format"
            )

    gates = plan.get("human_gates")
    if not isinstance(gates, list):
        errors.append("human_gates must be a list")
        gates = []
    gate_ids: set[str] = set()
    gate_by_id: dict[str, dict] = {}
    price_gates: list[dict] = []
    for index, gate in enumerate(gates):
        field = f"human_gates[{index}]"
        if not isinstance(gate, dict):
            errors.append(f"{field} must be an object")
            continue
        gate_id = gate.get("id")
        if not nonempty(gate_id):
            errors.append(f"{field}.id is required")
        elif gate_id in gate_ids:
            errors.append(f"{field}.id must be unique")
        else:
            gate_ids.add(gate_id)
            gate_by_id[gate_id] = gate
        if gate.get("kind") not in GATE_KINDS:
            errors.append(f"{field}.kind is invalid")
        elif gate.get("kind") == "price":
            price_gates.append(gate)
        if not nonempty(gate.get("item")):
            errors.append(f"{field}.item is required")
        status = gate.get("status")
        if status not in GATE_STATUSES:
            errors.append(f"{field}.status is invalid")
        if status == "approved" and not evidence_ref_allowed(
            gate.get("evidence_ref"),
            fixture_mode=fixture_mode,
        ):
            errors.append(
                f"{field}.evidence_ref must reference explicit human approval"
            )
        if status == "not-applicable" and not nonempty(gate.get("evidence_ref")):
            errors.append(f"{field}.evidence_ref is required for not-applicable")
        if completion and status == "pending":
            errors.append(f"{field}.status cannot be pending at completion")
    if has_price_signal(plan):
        if not price_gates:
            errors.append("human_gates requires a price gate for budget or currency content")
        elif completion and not any(
            gate.get("status") == "approved" for gate in price_gates
        ):
            errors.append("a price gate must be approved at completion")
    if recommendation_type == "evidence-acquisition":
        for gate_id in evidence_plan_gate_ids:
            if gate_id not in gate_ids:
                errors.append(
                    "evidence_acquisition_plan.approval_gate_ids references "
                    f"unknown gate {gate_id!r}"
                )
        budget_gate = gate_by_id.get(evidence_budget_gate_id)
        if budget_gate is None:
            if nonempty(evidence_budget_gate_id):
                errors.append(
                    "evidence_acquisition_plan.budget_range.approval_gate_id "
                    "references an unknown gate"
                )
        elif budget_gate.get("kind") != "price":
            errors.append(
                "evidence_acquisition_plan.budget_range.approval_gate_id "
                "must reference a price gate"
            )
        if evidence_plan_status == "approved":
            unresolved = [
                gate_id
                for gate_id in evidence_plan_gate_ids
                if gate_by_id.get(gate_id, {}).get("status") != "approved"
            ]
            if unresolved:
                errors.append(
                    "approved evidence_acquisition_plan requires all linked "
                    "human gates to be approved"
                )

    verification = plan.get("verification")
    if not isinstance(verification, dict):
        errors.append("verification must be an object")
        verification = {}
    for field in (
        "content_checks",
        "render_checks",
        "rendered_artifact_refs",
        "format_validation_refs",
        "revision_history",
    ):
        if not isinstance(verification.get(field), list):
            errors.append(f"verification.{field} must be a list")
    if verification.get("human_approval_required") is not True:
        errors.append("verification.human_approval_required must be true")
    if verification.get("independent_reader_required") is not True:
        errors.append("verification.independent_reader_required must be true")
    if verification.get("max_revision_cycles") != 2:
        errors.append("verification.max_revision_cycles must be 2")
    if verification.get("independent_reader_verdict_status") not in {
        "pending",
        "pass",
        "fail",
    }:
        errors.append("verification.independent_reader_verdict_status is invalid")
    if verification.get("final_approval_status") not in {"pending", "approved"}:
        errors.append("verification.final_approval_status is invalid")
    semantic_anchors = validate_semantic_anchors(
        verification,
        card=card,
        narrative=narrative,
        errors=errors,
    )

    if completion:
        task_dir: Path | None
        if base_dir is None:
            errors.append("base_dir is required for completion file verification")
            task_dir = None
        else:
            task_dir = Path(base_dir)
            if not task_dir.is_dir():
                errors.append("base_dir must resolve to the task directory")
                task_dir = None

        for field in ("content_checks", "render_checks"):
            values = verification.get(field)
            if (
                not isinstance(values, list)
                or not values
                or any(not nonempty(item) for item in values)
            ):
                errors.append(f"verification.{field} requires completion evidence")

        final_artifact_ref = verification.get("final_artifact_ref")
        final_artifact_sha256 = verification.get("final_artifact_sha256")
        text_extract_ref = verification.get("text_extract_ref")
        text_extract_sha256 = verification.get("text_extract_sha256")
        verdict_ref = verification.get("independent_reader_verdict_ref")
        verdict_sha256 = verification.get("independent_reader_verdict_sha256")

        for field, value in (
            ("editable_artifact_ref", verification.get("editable_artifact_ref")),
            ("final_artifact_ref", final_artifact_ref),
            ("text_extract_ref", text_extract_ref),
            ("independent_reader_verdict_ref", verdict_ref),
        ):
            if not nonempty(value):
                errors.append(f"verification.{field} is required at completion")
        for field in ("rendered_artifact_refs", "format_validation_refs"):
            values = verification.get(field)
            if (
                not isinstance(values, list)
                or not values
                or any(not nonempty(item) for item in values)
            ):
                errors.append(
                    f"verification.{field} requires task-relative completion paths"
                )

        for field, value in (
            ("final_artifact_sha256", final_artifact_sha256),
            ("text_extract_sha256", text_extract_sha256),
            ("independent_reader_verdict_sha256", verdict_sha256),
        ):
            if not valid_sha256(value):
                errors.append(
                    f"verification.{field} must be a lowercase SHA-256 digest"
                )

        if verification.get("independent_reader_verdict_status") != "pass":
            errors.append(
                "verification.independent_reader_verdict_status must be pass at completion"
            )
        if verification.get("final_approval_status") != "approved":
            errors.append("verification.final_approval_status must be approved at completion")
        if not evidence_ref_allowed(
            verification.get("final_approval_evidence_ref"),
            fixture_mode=fixture_mode,
        ):
            errors.append(
                "verification.final_approval_evidence_ref must reference explicit "
                "human approval"
            )

        rendered_refs = verification.get("rendered_artifact_refs")
        if isinstance(rendered_refs, list) and nonempty(final_artifact_ref):
            if normalized_ref(final_artifact_ref) not in {
                normalized_ref(value)
                for value in rendered_refs
                if nonempty(value)
            }:
                errors.append(
                    "verification.final_artifact_ref must appear in "
                    "rendered_artifact_refs"
                )

        if task_dir is not None:
            editable_path = resolve_task_file(
                verification.get("editable_artifact_ref"),
                base_dir=task_dir,
                field="verification.editable_artifact_ref",
                errors=errors,
            )
            if (
                editable_path is not None
                and not fixture_mode
                and job.get("output_format") in EDITABLE_SUFFIXES
                and editable_path.suffix.casefold()
                not in EDITABLE_SUFFIXES[job["output_format"]]
            ):
                expected = ", ".join(
                    sorted(EDITABLE_SUFFIXES[job["output_format"]])
                )
                errors.append(
                    "verification.editable_artifact_ref must end with "
                    f"{expected} for {job['output_format']}"
                )

            rendered_paths: list[Path] = []
            if not isinstance(rendered_refs, list) or not rendered_refs:
                errors.append(
                    "verification.rendered_artifact_refs requires completion evidence"
                )
            else:
                for index, reference in enumerate(rendered_refs):
                    resolved = resolve_task_file(
                        reference,
                        base_dir=task_dir,
                        field=f"verification.rendered_artifact_refs[{index}]",
                        errors=errors,
                    )
                    if resolved is not None:
                        rendered_paths.append(resolved)

            final_path = resolve_task_file(
                final_artifact_ref,
                base_dir=task_dir,
                field="verification.final_artifact_ref",
                errors=errors,
            )
            actual_final_hash = None
            if final_path is not None:
                actual_final_hash = sha256_file(final_path)
                if (
                    valid_sha256(final_artifact_sha256)
                    and actual_final_hash != final_artifact_sha256
                ):
                    errors.append(
                        "verification.final_artifact_sha256 does not match its file"
                    )

            text_extract_path = resolve_task_file(
                text_extract_ref,
                base_dir=task_dir,
                field="verification.text_extract_ref",
                errors=errors,
            )
            final_text = ""
            if text_extract_path is not None:
                actual_text_hash = sha256_file(text_extract_path)
                if (
                    valid_sha256(text_extract_sha256)
                    and actual_text_hash != text_extract_sha256
                ):
                    errors.append(
                        "verification.text_extract_sha256 does not match its file"
                    )
                try:
                    final_text = text_extract_path.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    errors.append(
                        "verification.text_extract_ref must be readable UTF-8 text"
                    )

            format_refs = verification.get("format_validation_refs")
            if not isinstance(format_refs, list) or not format_refs:
                errors.append(
                    "verification.format_validation_refs requires completion evidence"
                )
            else:
                for index, reference in enumerate(format_refs):
                    field = f"verification.format_validation_refs[{index}]"
                    receipt_path = resolve_task_file(
                        reference,
                        base_dir=task_dir,
                        field=field,
                        errors=errors,
                    )
                    if (
                        receipt_path is not None
                        and actual_final_hash is not None
                    ):
                        validate_format_receipt(
                            receipt_path,
                            final_artifact_sha256=actual_final_hash,
                            field=field,
                            errors=errors,
                        )

            verdict_path = resolve_task_file(
                verdict_ref,
                base_dir=task_dir,
                field="verification.independent_reader_verdict_ref",
                errors=errors,
            )
            verdict = None
            if verdict_path is not None:
                actual_verdict_hash = sha256_file(verdict_path)
                if (
                    valid_sha256(verdict_sha256)
                    and actual_verdict_hash != verdict_sha256
                ):
                    errors.append(
                        "verification.independent_reader_verdict_sha256 does not "
                        "match its file"
                    )
                verdict = read_json_file(
                    verdict_path,
                    field="verification.independent_reader_verdict_ref",
                    errors=errors,
                )
            if verdict is not None:
                errors.extend(
                    f"independent_reader_verdict: {error}"
                    for error in validate_reader_verdict(
                        verdict,
                        expected_artifact_sha256=actual_final_hash,
                    )
                )
                if normalized_ref(str(verdict.get("artifact_ref", ""))) != normalized_ref(
                    str(final_artifact_ref or "")
                ):
                    errors.append(
                        "independent_reader_verdict.artifact_ref must match "
                        "verification.final_artifact_ref"
                    )
                extraction = verdict.get("extraction")
                if isinstance(extraction, dict) and final_text:
                    validate_anchor_presence(
                        semantic_anchors,
                        final_text=final_text,
                        extraction=extraction,
                        errors=errors,
                    )

            validate_revision_history(
                verification.get("revision_history"),
                max_revision_cycles=2,
                base_dir=task_dir,
                final_artifact_ref=str(final_artifact_ref or ""),
                final_artifact_sha256=str(final_artifact_sha256 or ""),
                final_verdict_ref=str(verdict_ref or ""),
                final_verdict_sha256=str(verdict_sha256 or ""),
                errors=errors,
            )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a decision-ready material plan"
    )
    parser.add_argument(
        "--completion",
        action="store_true",
        help="verify final files, hashes, approvals, revision history, and reader evidence",
    )
    parser.add_argument(
        "--legacy-v2",
        action="store_true",
        help="validate an archived schema-v2 draft; cannot be used for completion",
    )
    parser.add_argument("plan")
    args = parser.parse_args()
    if args.completion and args.legacy_v2:
        print(
            json.dumps(
                {
                    "valid": False,
                    "errors": [
                        "--legacy-v2 cannot be combined with --completion"
                    ],
                }
            )
        )
        return 2
    plan_path = Path(args.plan)
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except Exception:
        print(
            json.dumps(
                {
                    "valid": False,
                    "errors": ["plan must be readable UTF-8 JSON"],
                }
            )
        )
        return 2
    errors = validate(
        plan,
        completion=args.completion,
        base_dir=plan_path.parent,
        fixture_mode=False,
        legacy_v2=args.legacy_v2,
    )
    print(
        json.dumps(
            {"valid": not errors, "errors": errors},
            ensure_ascii=False,
        )
    )
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
