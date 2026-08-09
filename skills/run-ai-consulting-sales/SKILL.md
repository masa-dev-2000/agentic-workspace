---
name: run-ai-consulting-sales
description: Prepare, conduct, close, and retrospect AI consulting sales meetings with commercially complete scope, price, effort, term, client burden, feasibility evidence, and next steps. Use for AI consulting 商談準備, 提案設計, 見積根拠, 価格設計, スコープ整理, PoC設計, クロージング, or 商談反省.
---

# Run AI Consulting Sales

## Purpose

Turn a technically plausible AI idea into a commercially decidable engagement. Minimize client decision effort while protecting delivery capacity and company economics.

## Classify the Conversation

Treat a meeting as a sales meeting when the other party is deciding whether, what, when, or how to buy. Do not enter such a meeting with technical research alone.

Choose one primary outcome:

- discovery: obtain missing facts needed for a proposal;
- sales decision: agree on direction, scope, price, term, and next action;
- delivery: execute or review already-contracted work.

State the intended outcome before preparing materials.

## Build the Commercial Brief

Read [preflight-checklist.md](references/preflight-checklist.md). Separate all inputs into:

1. verified facts;
2. inferences supported by facts;
3. hypotheses to validate.

Lead with the conclusion. Prepare:

- the client's desired business outcome and present bottleneck;
- the recommended first paid unit and why it comes first;
- later phases, clearly separated from the current offer;
- included work, exclusions, deliverables, and acceptance evidence;
- delivery period, estimated provider effort, and client participation time;
- meeting cadence and communication/approval method;
- fixed price or approved range, pricing rationale, and payment timing;
- feasibility risks and the minimum PoC needed;
- the exact decision requested in the meeting;
- owner, deadline, and completion signal for every next action.

Run:

`python scripts/audit_sales_brief.py <brief.md>`

Fix missing decision fields before the meeting.

## Price and Capacity

Read [pricing-capacity.md](references/pricing-capacity.md). Do not price from the operator's desired hourly wage alone.

Calculate internally:

- realistic delivery hours, including preparation, coordination, review, and rework;
- available monthly capacity and the number of clients that can be served safely;
- company fixed costs, sales/admin load, delivery risk, opportunity cost, and retained margin;
- negotiation target, floor, and any optional packages approved by the representative.

Explain the client-facing price through outcomes, bounded scope, access to specialist judgment, delivery risk, and avoided work. Never invent a discount or price during the meeting without internal authority.

## Design PoC Evidence

Decompose the workflow into independently testable capabilities. Label evidence precisely:

- desk research: public information supports feasibility;
- mock test: synthetic input proves internal logic only;
- sandbox test: vendor-provided test environment proves an integration path;
- live-account test: an authorized real account proves actual data access or action.

Do not call desk research or a mock demonstration a technical PoC. If value depends on email, social messaging, a carrier, payment, or another external system, include an authorized connection test. Define pass/fail criteria, evidence, required credentials, cost, time, and safety limits before running it.

## Run and Close the Meeting

Read [meeting-playbook.md](references/meeting-playbook.md).

In the meeting:

1. open with the recommended conclusion and decision agenda;
2. confirm facts and correct assumptions;
3. compare only viable directions;
4. state scope, exclusions, price, period, client burden, cadence, and PoC;
5. resolve objections without adding unpriced work;
6. close with a decision or an explicit owner, action, and deadline.

Afterward, record decisions and changes. Keep the estimate simple; place legal and operating conditions in the contract. Do not count verbal enthusiasm as agreement.

## Reduce Discovery Burden

Design discovery as a low-effort client experience.

- Bring a clearly labeled starting hypothesis and ask the client to correct it.
- Ask one decision-relevant question at a time and explain what the answer changes.
- Request original briefs, application forms, examples, and evaluation criteria before asking the client to restate them from memory.
- When names, sponsors, deadlines, or requirements are uncertain, record them as unknowns and obtain the source document; do not let a fuzzy recollection define scope.
- Translate “faster and higher quality” into observable acceptance evidence such as time to first draft, number of revision cycles, compliance with the brief, evaluator criteria coverage, differentiation, feasibility, and approval readiness.
- During peak periods, value reduced interruption and increased proposal throughput as well as hours saved.

For AI-assisted planning or proposal creation, define the minimum credible workflow:

1. ingest the official brief and constraints;
2. extract mandatory requirements and evaluation criteria;
3. generate multiple strategically distinct concepts;
4. compare them against evidence, feasibility, differentiation, and client fit;
5. develop the selected concept;
6. run adversarial review and revise;
7. require human approval before submission.

## Retrospective

Within one business day, answer:

- What decision was the meeting meant to produce, and was it produced?
- Which commercial item was missing or improvised?
- Which promise lacked evidence?
- Where did free scope or excessive accommodation enter?
- What concern did the client repeat?
- What should become a checklist, template, or PoC before the next meeting?

Convert repeated failures into a preflight rule. Preserve the distinction between a one-off outcome and a generalizable lesson.

## Guardrails

- Do not promise vague completion percentages such as “70% in one month.”
- Do not propose custom development before testing a lower-cost operational path.
- Do not blur advisory work, implementation, and deliverable-based development.
- Do not hide client work: state access setup, reviews, approvals, and meeting time.
- Do not compare the fee only with an employee salary; compare scope, commitment, risk, and specialist access.
- Do not use tool names in client documents unless technically or contractually necessary.
- Do not leave price, term, cadence, or the next decision unresolved in a sales meeting.
- Do not confuse performing the client's domain work with enabling the client to perform it using AI. For an AI-advisory offer, explicitly separate AI actions, client judgment, and advisor setup/support.
- When an actual domain task is used in the engagement, define it as a pilot for building and validating the AI operating method unless domain-deliverable production is separately scoped and priced.
