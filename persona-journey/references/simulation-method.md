# Persona journey simulation method

## Contents

1. Evidence boundary
2. Stakeholder and persona selection
3. Persona cards
4. Journey stages
5. Scene construction
6. Finding extraction
7. Prioritization and requirements
8. Required output
9. Quality checks

## 1. Evidence boundary

Start with a compact evidence register:

| Status | Meaning |
|---|---|
| Fact | Directly supplied by the user or artifact |
| Assumption | Plausible placeholder required to continue |
| Inference | Conclusion derived from facts or assumptions |
| Unknown | Material gap that should not be filled silently |

Ask before proceeding only when an unknown could change the stakeholder set, core goal, operating environment, or consequences of failure. Ask no more than three high-impact questions at once. For everything else, use explicit assumptions and continue.

## 2. Stakeholder and persona selection

Separate stakeholders into:

- direct users: personally interact with the product or workflow;
- influence stakeholders: authorize, purchase, regulate, manage, receive, audit, or support it without necessarily using the same interface.

Cluster direct users by experience-changing dimensions:

- goal and success criterion;
- authority and available actions;
- expertise and mental model;
- frequency and time pressure;
- device, location, connectivity, or accessibility conditions;
- cost and consequence of mistakes;
- dependence on other people or systems.

Generate between two and five personas. Begin with three. Merge personas whose journey, decisions, and requirement implications would be substantially identical. Add one only if it introduces a distinct path or risk. Explain the final count in one sentence.

Use the default coverage pattern when the evidence permits:

1. primary user: represents the central value proposition;
2. friction-sensitive user: low familiarity, low time, constrained context, or accessibility need;
3. operational or exception-path user: handles repeated use, administration, recovery, compliance, or edge conditions.

## 3. Persona cards

For each direct-user persona include only decision-relevant information:

- ID and short role label;
- situation and context;
- job to be done;
- success criterion;
- relevant knowledge and mental model;
- constraints and dependencies;
- consequence of failure;
- likely entry point;
- evidence status for each uncertain trait.

Do not add a name, age, gender, biography, personality type, or lifestyle detail unless it changes the simulated experience.

For each influence stakeholder record their interest, authority, information need, and point of influence. Do not force them into a direct-use narrative.

## 4. Journey stages

Test the full relevant lifecycle:

1. need or trigger;
2. discovery;
3. comparison and evaluation;
4. approval, purchase, or adoption;
5. registration, setup, migration, or onboarding;
6. first meaningful use;
7. repeated core use;
8. error, interruption, or exceptional condition;
9. diagnosis, support, and recovery;
10. collaboration, sharing, approval, or handoff;
11. continued use and habit formation;
12. abandonment, replacement, export, or exit.

Stages may be `not applicable`, but do not omit failure, recovery, or exit merely because the supplied design describes only the happy path.

Create a coverage matrix before the narratives. Use personas as rows and all twelve stages as columns. Each cell must contain a scene ID, `not applicable`, or `unknown`. The matrix is a completeness check, not a substitute for consequential scenes.

## 5. Scene construction

Assign each persona `P1`, `P2`, and so on. Assign scenes chronologically as `P1-S1`, `P1-S2`, and so on.

Each consequential scene should make this causal chain visible:

> Context → goal → available cue → action → system response → interpretation → decision → consequence

Include expectations and emotional or cognitive response where they affect action. Prefer concrete internal reactions such as uncertainty, loss of trust, relief, overload, or confidence over theatrical prose.

Respect the current-state boundary:

- If the specification does not define a system response, write `unknown response`.
- If a likely behavior is necessary to continue, mark it as an assumption.
- If the persona invents a workaround, distinguish it from product behavior.
- If a path ends, allow the persona to abandon, escalate, or switch tools.

## 6. Finding extraction

Extract findings only after completing all journeys so cross-persona patterns remain visible. Use IDs such as `F1`.

Classify each finding as one or more of:

- friction;
- unmet expectation;
- misunderstanding;
- missing information;
- accessibility or environmental barrier;
- unsafe or irreversible state;
- failed recovery;
- workaround;
- abandonment risk;
- stakeholder conflict;
- specification gap.

For each finding record:

- source scene IDs;
- affected personas and influence stakeholders;
- current behavior;
- expected behavior;
- consequence;
- evidence status: fact-based, assumption-dependent, or unknown;
- confidence: high, medium, or low;
- whether research is required before requirement promotion.

Do not count routine inconvenience and goal-blocking failure as equivalent.

## 7. Prioritization and requirements

Rank findings using ordinal judgments, not fabricated scores:

- blockage: prevents, delays, or merely inconveniences the core job;
- reach: one path, several personas, or nearly all users;
- frequency: known, plausibly recurring, rare, or unknown;
- consequence: annoyance, rework, lost trust, financial loss, safety, compliance, or irreversible data loss;
- confidence: strength of evidence and dependence on assumptions;
- recoverability: self-recoverable, support-dependent, or unrecoverable.

Use priorities:

- Critical: blocks the core job or creates severe, unsafe, or irreversible consequences.
- High: materially harms success for important personas or common paths.
- Medium: meaningful friction with a viable workaround.
- Low: improvement with limited effect on task completion.
- Validate first: potentially important but too assumption-dependent to specify responsibly.

Priority expresses user and operational impact, not delivery commitment. Do not infer an MVP boundary, release date, or mandatory bundle from simulation evidence alone. When requirements depend on one another, describe the dependency and leave release sequencing as a separate product decision.

Convert a finding into a requirement candidate with explicit traceability:

```text
R1 — [requirement statement]
Source: F2; scenes P1-S6, P3-S8
Affected users: P1, P3
Priority: High
Rationale: [observable consequence]
Evidence boundary: [fact/assumption/unknown]
```

For the top three to five candidates, add:

```text
User story: As [persona/role], I need [capability] so that [outcome].

Acceptance criteria:
- Given [precondition], when [action/event], then [observable result].
- Given [failure or edge condition], when [recovery action], then [observable recovery].
```

Avoid prescribing UI details unless the evidence requires them. State the outcome constraint first.

## 8. Required output

Use this order:

1. **Simulation target** — scope, current design, exclusions.
2. **Evidence boundary** — facts, assumptions, unknowns.
3. **Stakeholder structure** — direct users and influence stakeholders.
4. **Persona set** — cards and reason for the count.
5. **Journey coverage matrix** — every stage mapped to a scene, `not applicable`, or `unknown`.
6. **Current-state journeys** — chronological stories with scene IDs.
7. **Cross-journey findings** — traceable finding table.
8. **Persona and stakeholder conflicts** — incompatible goals or tradeoffs.
9. **Prioritized requirement candidates** — linked to findings and scenes.
10. **Detailed top requirements** — user stories and acceptance criteria for three to five items.
11. **Validation plan** — riskiest assumptions, research questions, and suggested tests.

For a very small feature, compress the sections but preserve traceability. For a complex service, keep persona journeys separate and consolidate findings afterward.

## 9. Quality checks

Before delivering, verify:

- The persona count reflects distinct experience paths, not arbitrary diversity.
- Every persona has a different reason to exist.
- The simulation covers failure, recovery, continued use, and exit.
- The coverage matrix accounts for every lifecycle stage without silent omission.
- No unspecified feature appears as current behavior.
- Facts, assumptions, inferences, and unknowns remain distinguishable.
- Emotional reactions follow from scenes rather than stereotypes.
- Every requirement points back to at least one finding and scene.
- Conflicts are surfaced instead of averaging personas into one fictional user.
- Uncertain high-impact claims become validation tasks, not confident requirements.
- Priority labels are not presented as release commitments.
- The output supports a product decision rather than ending at storytelling.
