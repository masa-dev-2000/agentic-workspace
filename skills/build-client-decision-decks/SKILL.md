---
name: build-client-decision-decks
description: Convert client interviews, meeting notes, sales discussions, proposals, implementation plans, and mixed stakeholder materials into decision-ready client-facing decks. Use when preparing or revising sales decks, meeting summaries, proposal decks, project alignment materials, executive briefings, statements of work, or implementation roadmaps; especially when a deck feels unfocused, mixes minutes with proposals or delivery plans, lists features instead of answering client questions, or needs to clarify recommendations, value, uncertainty, decision gates, client actions, scope, price, and next decisions.
---

# Build Client Decision Decks

Build the shortest evidence-backed story that moves the client to one clear decision. Treat visual design as downstream of communication design.

## Core rule

Define one communication job before editing:

- **Meeting summary:** confirm what was heard, agreed, unresolved, owned, and due.
- **Decision proposal:** enable a decision among options using recommendation, value, evidence, risk, and terms.
- **Delivery plan:** enable execution using milestones, dependencies, owners, inputs, governance, and gates.
- **Internal sales memo:** prepare closing strategy, negotiation boundaries, decision-makers, objections, and follow-up.

Do not combine these merely because the source material contains all of them. Split the deliverables or move supporting material to an appendix. Never expose an internal sales goal, negotiation tactic, margin constraint, or private assessment in a client-facing artifact.

## Workflow

### 1. State the decision

Write one sentence:

> After reviewing this material, the audience should be able to decide or confirm ___.

Identify the audience, actual decision-maker, decision deadline, value or risk at stake, loss from delay or error, and desired next action.

If no meaningful decision exists, classify the artifact as a meeting summary or information brief rather than pretending it is a proposal.

### 2. Reconstruct the client's question system

Extract the client's own questions, requested outcomes, concerns, objections, assumptions, and decision criteria. Cluster them by decision relevance, not conversation order.

For transformation or AI work, normally test these clusters:

- desired operational state;
- achievable scope and quality;
- time and milestones;
- cost and economic value;
- client effort and required inputs;
- uncertainty and method/date of resolution;
- risks, boundaries, and stop conditions;
- ownership, governance, and next decision.

Build a traceability matrix:

| Client said or asked | Underlying decision need | Direct answer | Evidence/status | Destination |
|---|---|---|---|---|

Use the client's wording where it preserves meaning. Answer every material request directly. Do not replace a requested answer with a generic capability description.

### 3. Separate claim types

Label working claims as:

- **Fact:** verified by source or direct observation.
- **Estimate:** calculated from stated inputs and formula.
- **Hypothesis:** plausible but unverified.
- **Recommendation:** proposed action or choice.
- **Unknown:** cannot yet be answered.

For every material estimate, show inputs, formula, range, and sensitivity. Never fill a numeric gap with unsupported precision.

For every decision-relevant unknown, state what is unknown, why it matters, how it will be tested, who owns the test, when it will be resolved, and what observation changes the decision.

### 4. Form the governing thought

Express the whole deck in one causal conclusion:

> Because [verified situation], we recommend [choice], which creates [business outcome], provided [critical condition].

This is the deck's spine. Each page must either prove it, qualify it, translate it into action, or enable the decision.

### 5. Select the story architecture

Read [references/architectures.md](references/architectures.md) and select only the architecture matching the communication job.

Default decision-proposal sequence:

1. client situation and decision;
2. desired future state and cost/risk of status quo;
3. recommendation and sequencing;
4. evidence and assumptions;
5. value and alternatives;
6. uncertainty, validation plan, and decision gates;
7. roadmap, client burden, and governance;
8. scope, commercial terms, and explicit next decision.

Default post-meeting summary sequence:

1. conclusion from the meeting;
2. “what you said / our answer”;
3. agreed priorities and sequencing;
4. unresolved questions and resolution dates;
5. each side's actions, owners, and deadlines;
6. confirmation requested.

### 6. Write answer-first pages

Use a conclusion as each page title. A title must communicate the takeaway without the body.

Prefer:

> Start with waybill automation; decide inquiry automation after a one-month quality test.

Avoid:

> Schedule  
> About the project  
> Our proposal

Build each page as:

1. governing sentence;
2. two to four supporting facts or implications;
3. source, assumption, or status;
4. decision consequence.

### 7. Connect activity to business value

Do not stop at hours saved or features delivered. Trace:

> intervention → operational change → capacity/risk/quality change → financial or strategic effect

Do not automatically monetize released time. State the condition under which time becomes cash, capacity, faster response, avoided hiring, lower risk, or growth.

Show alternatives, including status quo, where the decision is material. Define Base, Upside, and Downside when uncertainty changes economics or scope. State the observation that switches scenarios.

### 8. Make execution credible

For every workstream show:

- measurable outcome;
- milestone and date;
- client inputs and estimated effort;
- delivery owner;
- dependency;
- acceptance evidence;
- Go/Hold/Rework/No-Go gate;
- stop or exit condition.

Use a Gantt chart only after sequencing and dependencies are agreed. A timeline is evidence of execution logic, not the story itself.

### 9. Apply the client/internal boundary

Client-facing material may include their objectives, recommendation and rationale, facts and transparent assumptions, outcomes and measurement, client effort, uncertainty and validation, scope, price, terms, and next decision.

Keep internal:

- “close the contract” as the goal;
- negotiation floor and margin;
- private personality judgments;
- persuasion tactics;
- unagreed blame or speculation;
- internal resourcing problems;
- exposure analysis not intended for disclosure.

### 10. Run the decision-readiness audit

Before creating slides, test:

- Can the audience identify the requested decision in 30 seconds?
- Does every material client question have a direct answer or a dated resolution plan?
- Are fact, estimate, hypothesis, recommendation, and unknown distinguishable?
- Is the recommendation compared with status quo or a credible alternative?
- Is value connected to a business result rather than only activity?
- Are client effort, dependencies, owners, gates, and stop conditions visible?
- Does the deck have one communication job?
- Are internal goals and tactics excluded?
- Can any page be removed without weakening the decision? Remove it.

After the structure passes, use the relevant presentation or PDF skill to produce and visually verify the artifact.

## Screen-share mode

When the artifact will be shared on a PC screen, design for the viewer's reduced meeting-window size, not for printed-page density.

- Start in native 16:9. Do not place or center an A4 layout inside a wider page.
- Use one conversation or decision unit per page. Add pages before shrinking type.
- Default to at least 22 pt for body copy, 18 pt only for short supporting labels, and 32 pt or larger for the main takeaway.
- Limit the visible body to one conclusion plus at most three supporting items. Move detail into later pages or a separate handout.
- Avoid dense tables. Convert them into comparisons, progressive question pages, or one-row decision cards.
- Keep live prompts phrased so the presenter can ask them verbatim and the client can answer without decoding the page.
- Reserve a persistent, high-contrast area for the decision, question, or action expected on that page.
- Verify at a 50% scale or approximately 960x540 pixels. If ordinary body text is not immediately readable, redesign rather than merely enlarging the canvas.
- Inspect every rendered page for screen-share legibility, not only clipping and overlap.

## Meeting UX

Treat the deck as an interface for a live decision, not as a container for information.

- Minimize the client's reading, recall, interpretation, and input burden.
- Ask one answerable question at a time. Prefer recognition from concrete options over unaided recall, while always allowing correction or an unlisted answer.
- Pre-fill verified facts and clearly labeled hypotheses so the client edits a starting point instead of creating structure from zero.
- Show what input is needed, why it matters, and what decision it changes.
- Keep the current question, available choices, and expected output visible together.
- Convert vague requests into a progressive path: desired outcome → current workflow → constraints and evidence → options → selection → owner and deadline.
- Do not make the presenter translate a dense page. The visible wording should be usable verbatim in the meeting.
- Separate the live deck from a detailed handout when completeness would harm the meeting flow.

## Consulting standard

Do not mistake categorization for analysis. Convert client language into a decision-ready problem.

- Identify the decision, decision-maker, deadline, value at stake, bottleneck, and loss from delay or error.
- Distinguish facts, estimates, hypotheses, recommendations, and unknowns on the working layer; never promote a fuzzy recollection into a client-facing fact.
- Trace the recommendation causally: constraint → intervention → operational change → business outcome → critical condition.
- Compare the recommendation with status quo and at least one viable alternative when the choice is material.
- Connect saved time to the actual consequence: capacity during a peak period, response speed, proposal throughput, quality, risk, revenue opportunity, or avoided delay. Do not monetize time automatically.
- For creative or planning work, define quality through the target brief, evaluation criteria, evidence, differentiation, feasibility, and revision cycle—not through volume of ideas.
- Make unknown resolution executable: required document or observation, owner, date, and the result that would change the recommendation.
- End with the smallest credible next paid or operational unit, its inputs, acceptance evidence, client effort, and Go/Hold/Rework/No-Go gate.

### Preserve the service-role boundary

Before writing the recommendation, state which party performs the client's domain work.

- Distinguish **doing the domain work for the client** from **designing how the client uses AI to do that work**.
- If the offer is AI advisory, the consultant designs the AI workflow, prompts, evidence sources, review criteria, safeguards, training, and operating routine; the client retains domain judgment and approval.
- Do not silently turn AI enablement into tourism, marketing, legal, financial, HR, or other domain consulting.
- Show three separate lanes when ambiguity is possible: what AI does, what the client does, and what the AI advisor does.
- State both the proposed support and the client inputs or decisions required. A proposal is incomplete if either side's work is implicit.
- Use an actual client task as a pilot only to validate and transfer the AI-enabled method, unless production of the domain deliverable is explicitly included and priced.

## Output

When asked to analyze before editing, return:

1. communication-job diagnosis;
2. client's clustered questions and direct answers;
3. missing evidence and unknown-resolution plan;
4. recommended governing thought;
5. page-by-page architecture;
6. material to move to another document or appendix.

When asked to create or revise the artifact, implement the structure, verify traceability and visual output, then open the primary deliverable when appropriate.
