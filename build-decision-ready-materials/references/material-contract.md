# Material plan contract

Create `material-plan.json` after proposing the single Decision Card and before format
implementation. Schema version 3 separates draft structure from human approval and file-bound
completion evidence.

## Required shape

```json
{
  "schema_version": 3,
  "decision_card": {
    "status": "proposed | approved",
    "approval_evidence": "empty while proposed; explicit human evidence reference when approved",
    "decision": "Decision or action this material must cause",
    "recommendation": "Recommended choice",
    "why_now": "Consequence of acting or waiting",
    "largest_uncertainty": "Most decision-relevant unknown",
    "explicit_ask": "Observable next action",
    "chosen_format": "pptx | docx | pdf | google-slides | google-docs",
    "recommendation_type": "commit | bounded-pilot | evidence-acquisition | hold | reject | other"
  },
  "evidence_acquisition_plan": {
    "status": "proposed | approved",
    "owner_role": "Role accountable for the evidence program",
    "budget_range": {
      "amount_or_range": "Proposed cap or range",
      "proposal_basis": "How the proposal was derived",
      "assumptions": ["Visible planning assumption"],
      "range_or_sensitivity": "What moves the range",
      "approval_gate_id": "Price gate ID"
    },
    "start_by": "ISO date or explicit event",
    "return_decision_by": "ISO date or explicit event",
    "milestones": [
      {
        "id": "M1",
        "outcome": "Decision-relevant outcome",
        "owner_role": "Responsible role",
        "due_by": "ISO date or explicit event",
        "required_evidence": "Observable completion evidence"
      }
    ],
    "success_criteria": ["Evidence threshold that permits reconsideration"],
    "stop_conditions": ["Observation that ends the work"],
    "dependencies": ["Required access, approval, or prior result"],
    "final_decision_owner": "Role making the later go/no-go decision",
    "approval_gate_ids": ["G1"]
  },
  "communication_job": {
    "purpose": "What changes for the reader",
    "decision": "Decision or action this material must cause",
    "audience": ["Primary reader or stakeholder"],
    "use_moment": "live | async | fixed-distribution",
    "decision_owner": "Person or role with authority",
    "desired_action": "Observable next action",
    "deadline": "ISO date or explicit event",
    "stakes": "Economic, operational, strategic, or risk consequence",
    "failure_cost": "What is lost if the material fails",
    "output_format": "pptx | docx | pdf | google-slides | google-docs",
    "format_basis": "explicit-user-request | live-discussion | async-close-reading | fixed-distribution",
    "format_request_evidence_ref": "user-message:required only for explicit-user-request"
  },
  "claims": [
    {
      "id": "C1",
      "statement": "One decision-relevant claim",
      "classification": "fact | estimate | hypothesis | recommendation | unverified",
      "evidence_refs": ["source locator, or supporting claim IDs for a recommendation"],
      "method": "Required for estimates",
      "range_or_sensitivity": "Required for estimates",
      "confidence": "high | medium | low",
      "counterevidence_or_gap": "Contradiction, limit, or missing evidence"
    }
  ],
  "narrative": {
    "governing_thought": "One-sentence answer",
    "causal_chain": ["Cause", "mechanism", "consequence"],
    "recommendation": "Recommended choice",
    "primary_claim_ids": ["A known, non-unverified recommendation claim"],
    "required_messages": ["Meaning that a blind reader must recover"],
    "alternatives": [
      {"name": "Status quo", "tradeoff": "Consequence and opportunity cost"}
    ],
    "explicit_ask": "Decision, approval, resource, or action requested"
  },
  "architecture": [
    {
      "id": "U1",
      "reading_job": "What this unit changes for the reader",
      "headline": "Conclusion-led headline",
      "claim_ids": ["C1"],
      "representation": "prose | bullets | table | chart | image | diagram | timeline",
      "speaker_or_appendix_detail": "Optional supporting detail"
    }
  ],
  "human_gates": [
    {
      "id": "G1",
      "kind": "price | legal | external-claim | production | other",
      "item": "Consequential value, statement, or commitment",
      "status": "approved | not-applicable | pending",
      "evidence_ref": "user-message:opaque approval reference"
    }
  ],
  "verification": {
    "content_checks": ["Observed content check"],
    "render_checks": ["Observed rendered check"],
    "editable_artifact_ref": "task-relative editable artifact path",
    "rendered_artifact_refs": ["task-relative rendered artifact path"],
    "final_artifact_ref": "rendered artifact supplied to the blind reader",
    "final_artifact_sha256": "lowercase SHA-256",
    "text_extract_ref": "task-relative UTF-8 final text extract",
    "text_extract_sha256": "lowercase SHA-256",
    "format_validation_refs": ["task-relative format validation receipt"],
    "human_approval_required": true,
    "independent_reader_required": true,
    "max_revision_cycles": 2,
    "independent_reader_verdict_ref": "task-relative reader-verdict.json",
    "independent_reader_verdict_sha256": "lowercase SHA-256",
    "independent_reader_verdict_status": "pending | pass | fail",
    "final_approval_status": "pending | approved",
    "final_approval_evidence_ref": "user-message:opaque final approval reference",
    "semantic_anchors": {
      "decision": ["short stable phrase from the approved decision"],
      "recommendation": ["short stable phrase from the approved recommendation"],
      "why_now": ["short stable phrase from approved why-now"],
      "largest_uncertainty": ["short stable phrase from the approved uncertainty"],
      "explicit_ask": ["short stable phrase from the approved ask"],
      "required_messages": ["short stable phrase from required messages"]
    },
    "revision_history": [
      {
        "revision": "r0",
        "artifact_ref": "task-relative reviewed artifact",
        "artifact_sha256": "lowercase SHA-256",
        "reader_verdict_ref": "task-relative reader verdict",
        "reader_verdict_sha256": "lowercase SHA-256",
        "verdict": "fail | pass"
      }
    ]
  }
}
```

## Deterministic evidence rules

- `evidence_acquisition_plan` is required only when
  `decision_card.recommendation_type` is `evidence-acquisition`; otherwise it is `null`.
- `bounded-pilot` means the requested decision is authorization of a time-boxed discovery, test,
  or pilot. `evidence-acquisition` means the substantive decision is deferred and the current ask
  is authorization to collect the evidence needed to return that decision.
- Evidence acquisition must name an owner, proposed budget range with basis, assumptions and
  sensitivity, start and return-decision timing, milestones, success and stop conditions,
  dependencies, final decision owner, and human gates.
- The budget gate is a `price` gate. An approved evidence plan cannot point to pending gates,
  and completion rejects a merely proposed plan.
- Proposed numeric ranges are decision proposals, not facts. Keep their derivation and
  assumptions visible and do not use them as external claims before approval.

- A fact has a source reference.
- An estimate has source references, method or formula, and range or sensitivity.
- A hypothesis names uncertainty and a disconfirming observation.
- A recommendation references known supporting claim IDs and includes at least one fact,
  estimate, or hypothesis. It cannot rely on an unverified claim.
- `narrative.primary_claim_ids` is explicit. An unverified claim cannot be primary.
- Budget, price, cost, currency-code, currency-symbol, or equivalent Japanese signals require a
  `price` human gate. It must be approved before completion.
- An explicit format override requires `format_request_evidence_ref`. Merely writing
  `format_basis: explicit-user-request` is insufficient.
- A draft Decision Card may be `proposed` with empty approval evidence. Completion requires
  `approved`; approval must never be inferred from the request to create the material.
- Approved Decision Card, consequential gate, and final approval references use an explicit
  human-evidence prefix such as `user-message:`, `approval-record:`, or `human-approval:`.

## Completion file binding

All completion references are resolved relative to the directory containing
`material-plan.json`. Absolute paths, parent traversal, missing paths, directories, and zero-byte
files fail. In production, the editable artifact suffix must match the chosen format (`.pptx`,
`.docx`, or `.pdf`; Google formats use the corresponding editable export). The production CLI
has no fixture-mode option.

`final_artifact_sha256`, `text_extract_sha256`, and
`independent_reader_verdict_sha256` must match their files. The reader verdict's
`artifact_ref` and `reviewed_artifact_sha256` must match the declared final artifact. Each format
validation reference is a JSON receipt with:

```json
{
  "schema_version": 1,
  "status": "pass",
  "artifact_sha256": "the final artifact digest",
  "checks": ["non-empty structural or rendered check evidence"]
}
```

Semantic anchors are deliberately short, stable phrases copied from the approved meanings. The
completion validator requires every anchor in both the final UTF-8 text extract and the mapped
blind-reader extraction field. This deterministic comparison supplements, rather than pretends
to replace, reader judgment.

`revision_history` starts at `r0`, has no gaps, and contains at most the initial artifact plus two
material changes (`r0` through `r2`). Artifact and verdict hashes and reviewer-run references are
unique; review timestamps increase; all prior revisions fail; the final revision passes and
matches the final artifact and verdict fields.

Tests may call the Python validation function with `fixture_mode=True` to accept explicit
`fixture:` or `synthetic-fixture:` approval references. That bypass is unavailable through the
production CLI and never belongs in a real material plan.

Run the production completion verifier only against the final task-local state:

`python scripts/validate_material_plan.py --completion material-plan.json`

Archived schema-v2 drafts can be inspected with
`python scripts/validate_material_plan.py --legacy-v2 material-plan.json`.
Legacy mode cannot be combined with completion and must not be used for new work.
