---
name: phase-gate-manager
description: Define, inspect, and govern phase-specific product requirements, exit criteria, evidence, and Go/Hold/Rework/Kill transitions across Discovery, PoC, MVP, Product, and Extension. Use when a project needs lifecycle phases, phase requirements, readiness reviews, maturity gates, PHASES.md creation or validation, or a decision about advancing to the next product stage.
---

# Phase Gate Manager

Keep product intent stable while changing requirements and quality thresholds by phase. Treat this
Skill as a specialist under `project-orchestrator`; do not create a second project-management
control loop.

## Workflow

1. Read `README.md`, `REQUIREMENTS.md`, `ROADMAP.md`, `TODO.md`, `DECISIONS.md`, repository
   evidence, and the AI Project Manager ledger when present.
2. Read [phase-model.md](references/phase-model.md).
3. Locate `PHASES.md`. If missing, propose creating it from
   [PHASES.template.md](assets/PHASES.template.md); do not overwrite an existing file.
4. Identify the current phase from evidence. Never infer advancement from elapsed time or task
   count alone.
5. Separate:
   - permanent product requirements;
   - phase-specific requirements;
   - exit criteria;
   - evidence;
   - gate decision.
6. Keep requirement IDs stable. Attach every executable task to a `phaseId` and one or more
   `requirementIds` when the ledger supports them.
7. Validate before proposing a gate decision:

```powershell
node scripts/phase-gate.mjs validate PATH\TO\PHASES.md
```

8. Report unmet exit criteria, missing evidence, contradictions, and downstream effects.
9. Recommend exactly one of `GO`, `HOLD`, `REWORK`, or `KILL`. A recommendation is not approval.
10. Record an approved decision in `PHASES.md` and append its rationale to `DECISIONS.md`.
11. Re-plan `ROADMAP.md` and current `TODO.md` for the selected phase. Preserve prior phase
    requirements and evidence as history.

## Gate rules

- `GO`: every mandatory exit criterion is met with referenced evidence.
- `HOLD`: the phase remains valid but an external dependency or timing constraint blocks it.
- `REWORK`: evidence falsifies or weakens a material assumption and more work is justified.
- `KILL`: the project or branch no longer has sufficient value, feasibility, authority, or fit.
- Never convert an unchecked criterion into evidence.
- Never use a document's existence as proof that its claim is true.
- Never lower product-wide safety, legal, security, or data requirements for an early phase.
- Allow PoC shortcuts only when explicitly scoped, reversible, and prohibited from production use.
- Treat each material Extension as a nested mini-cycle, not an automatic final phase.

## Document ownership

- `REQUIREMENTS.md`: permanent requirements and constraints.
- `PHASES.md`: phase questions, scoped requirements, exits, evidence, and decisions.
- `ROADMAP.md`: sequence and timing of phases and milestones.
- `TODO.md`: current executable work only.
- `DECISIONS.md`: approved gate decisions and material trade-offs.
- AI Project Manager ledger: live task state, assignment, dependencies, deadlines, and verification.

Do not duplicate live task status across all documents. Generate human-readable summaries from the
ledger, while keeping durable rationale and evidence references in project documents.

## Initialize cautiously

To create `PHASES.md` only when absent:

```powershell
node scripts/phase-gate.mjs init PATH\TO\PROJECT
```

Preview the template or requested semantic changes before applying them when project history
already exists.
