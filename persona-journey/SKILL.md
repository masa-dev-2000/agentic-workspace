---
name: persona-journey
description: Simulate how automatically generated user personas experience a current product concept, specification, workflow, or requirements set from need through discovery, adoption, core use, failure, recovery, continued use, and exit; then extract traceable friction points, unmet expectations, conflicts, hypotheses, prioritized requirements, user stories, and acceptance criteria. Use when refining requirements, imagining current-state usability, pressure-testing a proposed service or feature through stakeholder-specific journeys, or asking what using the present design would feel like. Do not use for marketing personas alone, generic customer-journey diagrams without requirement analysis, or fictional storytelling unrelated to product decisions.
---

# Persona Journey

Simulate the current design through distinct users before recommending changes. Keep facts, assumptions, and inferences separate. Make every finding traceable to a scene.

## Establish the simulation boundary

1. Identify the product, feature, workflow, or requirement set being tested.
2. Treat supplied artifacts as the current design. Do not silently add convenient behavior.
3. Ask at most three questions only when missing information could materially change the personas or journey. Otherwise proceed with labeled assumptions.
4. State what is known, assumed, inferred, and unknown.

Read [simulation-method.md](references/simulation-method.md) before performing the simulation.

## Generate personas from the stakeholder structure

Cluster direct users by materially different goals, authority, expertise, environment, frequency, consequences, or accessibility needs. Do not create one persona per named stakeholder.

- Generate 2 to 5 direct-user personas.
- Use 3 by default: primary user, friction-sensitive user, and operational or exception-path user.
- Add a persona only when it exercises a meaningfully different experience path.
- Record purchasers, approvers, regulators, managers, and support staff as influence stakeholders when they do not directly use the product.
- Avoid decorative demographics. Include a trait only when it changes behavior or requirements.

## Run an end-to-end journey

For each persona, cover the complete relevant lifecycle: need, discovery, evaluation, adoption, onboarding, first use, core task, error, recovery, continued use, sharing or handoff, and exit. Mark stages `not applicable` or `unknown` instead of inventing details.

Before writing the stories, include a compact coverage matrix mapping every lifecycle stage for every persona to a scene ID, `not applicable`, or `unknown`. Never omit a stage silently.

Write a concrete chronological experience, not a generic journey map or embellished short story. At every scene show:

- what the persona is trying to accomplish;
- what they see or know under the current design;
- what they do and decide;
- what they expect;
- what actually happens;
- their cognitive or emotional response;
- whether they advance, recover, work around, or leave.

Assign stable scene IDs such as `P2-S4`.

## Extract requirement evidence

After all journeys, derive:

1. friction points, unmet expectations, misunderstandings, unsafe states, recovery failures, workarounds, and exits;
2. conflicts between personas or stakeholders;
3. requirement candidates linked to scene IDs;
4. uncertain claims that need research or usability testing rather than immediate requirement status.

Prioritize by task blockage, reach, likely frequency, consequence, evidence confidence, and recoverability. Avoid fake numeric precision.

For every finding, include the source scene, affected personas, evidence status, impact, and why the current design causes it. For the top three to five requirement candidates, add a user story and testable Given/When/Then acceptance criteria.

## Preserve the distinction between present and future

Complete the current-state simulation before proposing improvements. Never narrate a proposed improvement as though it already exists. Label future-state behavior explicitly.

Do not present invented user behavior, demographics, market facts, or frequency estimates as evidence. Do not generalize one persona's preference to every user. Do not replace real user research with the simulation; identify the riskiest assumptions to validate next.

Keep requirement priority separate from release scope. A simulation can identify critical candidates but cannot by itself commit them to an MVP or release. State sequencing dependencies and validation needs instead of declaring a release decision unless the user explicitly asks for one.

## Deliver the result

Use the output order defined in [simulation-method.md](references/simulation-method.md). Keep the journeys vivid enough to expose decisions and friction, but spend more space on consequential scenes than routine transitions. Do not append an unrelated generic technical explanation.
