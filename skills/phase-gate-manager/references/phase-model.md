# Phase model

Use phases as uncertainty-reduction contracts, not calendar labels.

| Phase | Primary question | Typical requirements | Exit evidence |
|---|---|---|---|
| Discovery | Is the problem worth solving? | user, problem, context, constraints, success measure | research and an explicit pursue/stop decision |
| PoC | Can the riskiest technical claim work? | hypothesis, experiment, threshold, prohibited production use | reproducible experiment and known failure boundary |
| MVP | Does the smallest usable solution create value? | target user, core journey, minimum trust and data safety | observed use, outcome signal, prioritized learning |
| Product | Can it be operated responsibly at intended scale? | reliability, security, privacy, accessibility, support, monitoring | release verification and operating readiness |
| Extension | Does a bounded expansion add net value without harming the core? | new segment/capability, compatibility, migration, regression boundary | incremental outcome and regression evidence |

## Requirement inheritance

Classify every requirement:

- `permanent`: applies from introduction through retirement;
- `phase-only`: applies only to one experiment or maturity level;
- `introduced`: begins in this phase and remains inherited;
- `deferred`: intentionally excluded until a named phase, with risk and owner.

Early phases may reduce breadth and operational polish, but must not silently waive permanent
safety, legal, security, privacy, or data-integrity constraints.

## Gate contract

Each phase needs:

1. one primary question;
2. bounded in-scope and out-of-scope work;
3. uniquely identified requirements;
4. measurable exit criteria;
5. evidence references;
6. unresolved risks;
7. a decision and rationale;
8. consequences for roadmap, tasks, resources, and the next phase.

Use `GO`, `HOLD`, `REWORK`, or `KILL`. Keep the previous decision immutable; append a superseding
decision if later evidence changes it.

## Ledger projection

Where supported, add these fields to tasks without removing the existing task contract:

```json
{
  "phaseId": "mvp",
  "requirementIds": ["MVP-003"],
  "gateId": "mvp-exit",
  "evidenceIds": ["EV-021"]
}
```

The ledger owns live task state. `PHASES.md` owns the durable phase contract and gate history.
