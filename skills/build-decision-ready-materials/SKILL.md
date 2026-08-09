---
name: build-decision-ready-materials
description: Turn source evidence and sparse intent into decision-ready stakeholder materials with an explicit communication job, claim-evidence map, causal narrative, format routing, and verified content and visual quality. Use for proposals, executive briefs, board materials, sales decks, reports, decision memos, workshop handouts, PPTX, DOCX, or PDF deliverables where creating a polished file is insufficient and the material must drive a decision, approval, alignment, funding, or action.
---

# Build decision-ready materials

Own the communication outcome. Delegate file-format implementation and rendering to the
applicable PPTX, DOCX, or PDF Skill.

## Build from the decision backward

1. Identify the decision or action the material must cause, the audience, use moment, decision
   owner, deadline, stakes, failure cost, and evidence standard. Infer low-risk omissions from
   supplied evidence. Use `research.deep.align-execute` only when an evidence gap could change
   the recommendation; do not research merely to decorate the artifact.
2. Route the format deterministically with [format-routing.md](references/format-routing.md).
   An explicit user format wins. Otherwise use PPTX for live discussion, DOCX for asynchronous
   close reading, and PDF for fixed distribution. Produce only one canonical format unless the
   user asked for more.
3. Before expensive production, show exactly one compact **Decision Card** containing:
   decision, recommendation, why now, largest uncertainty, explicit ask, chosen format, and any
   consequential human gates. Classify the recommendation as `commit`, `bounded-pilot`,
   `evidence-acquisition`, `hold`, `reject`, or `other`. Use `bounded-pilot` when the decision
   itself is whether to authorize a time-boxed discovery, test, or pilot, even when learning is
   its purpose. Use `evidence-acquisition` when the requested substantive decision cannot yet be
   made and the recommendation is instead to authorize work that returns that decision. For
   `evidence-acquisition`, include one
   compact proposed execution block covering owner, budget range and basis, timing, milestones,
   success and stop conditions, dependencies, final decision owner, and approval gates. Keep
   unsupported numeric ranges visibly proposed with assumptions and sensitivity. Ask for one
   approval of this card. Until explicit human approval is received, keep the Decision Card
   `proposed` with no approval evidence; never infer approval from the source request. If the user has already
   explicitly approved the same decision, recommendation, ask, and format in the current
   request, record that evidence and continue without asking again.
4. Inspect the authoritative source set before drafting. Separate facts, estimates, hypotheses,
   recommendations, and unverified claims. Never use polished wording to hide missing evidence.
   Price, legal language, external claims, production commitments, and other consequential
   assumptions remain explicit human gates.
5. After Decision Card approval, create a task-local `material-plan.json` using
   [material-contract.md](references/material-contract.md). Validate it with:

   `python scripts/validate_material_plan.py material-plan.json`

   A proposed evidence-acquisition plan may remain a draft, but it cannot complete until its
   status and linked human gates are approved.
6. Write the governing thought first, then the causal chain that makes it true. Include the
   status quo and material alternatives, opportunity cost, risks, dependencies, and the explicit
   ask when relevant.
7. Give every page, slide, or section one reading job and one conclusion-led headline. Remove
   content that does not change understanding, belief, decision, or action.
8. Delegate implementation through the selected format capability. Preserve user templates and
   brand constraints. Validate content before visual polish, then render and inspect the complete
   artifact through the delegated format Skill.
9. Run the independent-reader protocol in
   [blind-reader-review.md](references/blind-reader-review.md). A fresh reviewer agent receives
   only the final rendered artifact and the generic review contract—never this plan, the source
   evidence, draft conversation, expected answer, or suspected defect. Validate its structured
   verdict and bind it to the reviewed artifact SHA-256 with:

   `python scripts/validate_reader_verdict.py reader-verdict.json`

   Record the initial artifact as `r0`; each materially changed artifact gets the next sequential
   revision, a unique artifact hash, a fresh reviewer run, and a hash-bound verdict. Compare the
   declared semantic anchors with both the final text extract and blind extraction. Allow at most
   two materially changed cycles after `r0`. If a fresh independent reviewer is unavailable or
   the artifact still fails, return it as a draft with blockers; never claim completion.
10. Run the completion form of the deterministic plan check:

    `python scripts/validate_material_plan.py --completion material-plan.json`

    This production check resolves task-relative artifact, render, text-extract,
    format-validation, and verdict files; verifies their required hashes and revision history;
    and rejects fixture-only approval references. Ask the human only for unresolved
    consequential gates or the final approval boundary, and record the final approval evidence
    reference. Deliver the artifact and compact evidence summary, not planning debris.

## Non-negotiable gates

- **Decision gate:** The intended reader can identify the decision, recommendation, rationale,
  consequence of delay, and requested action without presenter explanation.
- **Evidence gate:** Every material factual claim is traceable; every estimate exposes inputs,
  formula or method, range, and sensitivity; unknowns remain visible.
- **Reasoning gate:** Conclusions have causal support, alternatives include the status quo, and
  counterevidence or disconfirming conditions are not suppressed.
- **Narrative gate:** Sequence follows audience decision logic, not the chronology of the work or
  a generic template.
- **Density gate:** Use the lightest representation that preserves meaning. Do not solve excess
  content by shrinking type or filling every available area.
- **Visual gate:** Render every page or slide and inspect at full size. Fix clipping, overlap,
  illegibility, weak hierarchy, broken tables or charts, and misleading emphasis.
- **Action gate:** Name the decision owner, next action, timing, dependencies, and approval or
  resource request when the artifact is meant to trigger execution.
- **Blind-reader gate:** A context-isolated reader can recover the approved decision,
  recommendation, rationale, uncertainty, and ask from the rendered artifact alone.

Do not mark completion from a plan, generated source, successful export, or self-review alone.
Completion requires the final artifact, format-specific structural and rendered evidence, a
passing independent-reader verdict, resolved consequential human gates, and recorded final
approval state. External sending or publishing remains outside this Skill unless separately
authorized.
