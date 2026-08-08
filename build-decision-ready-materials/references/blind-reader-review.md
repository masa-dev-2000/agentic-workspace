# Independent blind-reader review

Use a fresh independent agent after the artifact has been rendered. The reviewer must not have
participated in discovery, planning, drafting, implementation, or prior review.

## Context boundary

Give the reviewer only:

- the complete final rendered artifact;
- this generic instruction: “Read this as the intended decision-maker. Extract what decision is
  being requested, the recommendation, the causal rationale, the largest visible risk or
  uncertainty, the requested next action, and its timing. Decide whether you could act without a
  presenter. Cite artifact locations and report blocking ambiguity or unsupported claims.”
- the output shape below.

Do not give the reviewer the material plan, Decision Card, source set, expected extraction,
draft history, author commentary, suspected defect, or prior verdict.

## Required output

```json
{
  "schema_version": 1,
  "artifact_ref": "task-relative final rendered artifact path",
  "reviewer_run_ref": "agent-run:opaque fresh-review identifier",
  "reviewed_at": "ISO-8601 timestamp with timezone",
  "reviewed_artifact_sha256": "lowercase SHA-256 of the reviewed artifact",
  "review_context": {
    "independent_reviewer": true,
    "received": ["final-rendered-artifact"]
  },
  "extraction": {
    "decision": "What must be decided",
    "recommendation": "What should be chosen",
    "causal_rationale": "Why",
    "largest_risk_or_uncertainty": "Visible limitation or unknown",
    "explicit_ask": "Observable next action",
    "deadline_or_timing": "When"
  },
  "locators": ["page or slide and visible heading"],
  "can_act_without_presenter": true,
  "blocking_issues": [],
  "nonblocking_observations": [],
  "verdict": "pass"
}
```

`verdict` is `pass` or `fail`. A pass requires a complete extraction, at least one locator,
`can_act_without_presenter: true`, and no blocking issue. The authoring agent validates the JSON,
hash-binds it to the final artifact, then compares the declared semantic anchors with both the
final text extract and the blind extraction. Each revision uses a new `reviewer_run_ref`; reuse is
rejected.

## Revision boundary

Count only materially changed artifacts as revision cycles. After two failed cycles, stop and
return the artifact as a draft with the blind-reader evidence and unresolved blockers. Never
weaken the expected answer or expose it to the reviewer to obtain a pass.

Record the initial artifact as `r0`. The final completion history may contain only `r0`, `r0` and
`r1`, or `r0` through `r2`; revision labels have no gaps, hashes are unique, prior verdicts fail,
and the final verdict passes.
