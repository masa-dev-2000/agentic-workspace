# Format routing

Choose the format from the reading and decision environment, not from habit.

Run `python scripts/route_material_format.py --use-moment <live|async|fixed-distribution>`.
When the user explicitly requested a format, add
`--requested-format <pptx|docx|pdf|google-slides|google-docs>`; that explicit format wins.
Record the exact current-turn request or approval reference in
`communication_job.format_request_evidence_ref`. Production plan validation rejects an
`explicit-user-request` basis without that evidence.
Without an explicit request, the deterministic defaults are:

| Use moment | Canonical format |
| --- | --- |
| live | PPTX |
| async | DOCX |
| fixed-distribution | PDF |

Create one canonical format by default. Multiple formats require an explicit user need; they
share one claim map but adapt density and sequence to each reading environment.

## PPTX or Google Slides

Use for live discussion, executive review, approval meetings, sales conversations, or a
page-by-page persuasive sequence. Resolve the stable `presentation.create` capability and
delegate implementation and render QA to its current provider. A user template or explicit brand
direction overrides generic visual guidance.

## DOCX or Google Docs

Use for asynchronous close reading, decision records, detailed proposals, operating plans,
formal reports, or content that must be commented on and revised. Delegate implementation,
style selection, rendering, and structural QA through the stable `document.create` capability.

## PDF

Use as a fixed final distribution format, printable artifact, or when layout must not change.
Prefer authoring in PPTX or DOCX when that better matches the content, then export and verify
the PDF. Delegate PDF rendering, form integrity, and final page inspection through the stable
`pdf.create` capability.

## Mixed deliverables

Create one canonical argument and evidence map. Adapt density and sequencing for each format;
do not mechanically paste slide copy into a memo or memo prose into slides. Keep conclusions,
numbers, definitions, and source identities consistent across versions.
