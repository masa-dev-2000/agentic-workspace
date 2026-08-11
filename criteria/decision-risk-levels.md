---
id: decision-risk-levels
statement: An automated decision may run unattended only at the risk level its recorded human-review agreement rate supports, and every automated decision must record its basis and confidence so that agreement can be measured at all.
status: proposed
version: 1
---

## Rationale

Automating judgment fails in two opposite ways. Route everything through a human and
the automation is theatre — the human rubber-stamps and the loop adds latency without
adding safety. Automate everything and errors accumulate silently, because nothing
compares the machine's answer to the answer a human would have given.

The way out is not to guess which decisions are safe. It is to make every automated
decision *measurable*: record what was decided, on what basis, and how confident the
decider was, then let the measured agreement rate decide how much autonomy that class
of decision earns. Autonomy becomes an outcome of evidence rather than an assumption.

Self-reported confidence matters as much as the decision itself: reviewing only the
low-confidence decisions is what keeps human review affordable as volume grows.

## Scope

Applies to decisions made by the agents in `agents/claude/` during unattended or
semi-attended runs — issue triage, priority assignment, duplicate detection, evidence
sufficiency, criterion applicability. Also applies to any future agent that classifies,
prioritises, or dispositions work items.

Does not apply to: irreversible operations (those are governed by the countermeasure
type in the ops rulebook), or to a human's own decisions.

## Risk levels

Cost of being wrong sets the level, not how hard the decision is.

| Level | Examples | Autonomy |
|---|---|---|
| **L1 low** | priority assignment, lens classification, duplicate detection | Runs unattended. Record only. |
| **L2 medium** | rejecting a candidate for weak evidence, closing an issue, marking `needs-criterion` | Runs unattended, but **every decision is reviewed** in the weekly cycle. |
| **L3 high** | activating a criterion, hiring or retiring an agent, changing a risk level | **Proposal only.** A human executes. |
| **L4 highest** | merging a PR, deleting data, sending externally, deploying | **Human only.** The agent may present evidence, never decide. |

A decision class not yet listed starts at **L2**. Never at L1 — a class with no
measured agreement rate has no evidence entitling it to run unreviewed.

## Required record

Every L1/L2 decision records: decision class, what was examined, the conclusion, the
basis (criterion id, or explicitly `heuristic` when no criterion applied), self-reported
confidence (high/medium/low), and the risk level. The review outcome (agree /
disagree + the correct answer) is appended later by the reviewer.

A decision that cannot be recorded must not be automated. Unrecorded automation cannot
be improved, only trusted — and trust without measurement is what this criterion exists
to prevent.

## Promotion and demotion

- **Promote L2 → L1** when the last 20 reviewed decisions of that class agree at ≥90%.
- **Demote L1 → L2** when agreement falls below 80%, or on any single disagreement whose
  consequence was material.
- Promotion and demotion are themselves L3: proposed by the machine, executed by a human.

Thresholds are initial values, to be revisited once real review data exists. Revisiting
them is also L3.

## Handling disagreement

Every disagreement resolves into exactly one of three causes, and the fix differs:

1. **No criterion existed** → criteria-steward drafts one.
2. **The criterion was misread** → add the counterexample to that criterion.
3. **The risk level was too generous** → demote the class.

"The agent got it wrong" is not an accepted resolution. It stops at blame and changes
nothing — the same failure mode the ops rulebook rejects for human mistakes.

## Counterexamples / Boundaries

- A high agreement rate on a trivial sample proves nothing; 20 decisions is a floor for
  promotion, not a target to rush toward.
- Agreement with a human is not the same as correctness. If reviewer and agent share a
  blind spot, the rate stays high while both are wrong — which is why disagreements are
  mined for missing criteria rather than merely counted.
- Confidence is self-reported and can be miscalibrated. Track whether low-confidence
  decisions really do disagree more often; if they do not, the confidence signal is
  worthless and reviewing by confidence must stop.

## Evidence

- 7 issues (#1–#7) were triaged with priority and lens labels, all marked
  `needs-criterion`: decisions were already being automated with no recorded basis and
  no review, which is the gap this criterion closes.

## Status History

- 2026-08-11: drafted as proposed.
