---
name: proposal-proportionality-gate
description: Assess whether a proposed architecture, security control, operational gate, migration, release process, or remediation is necessary and proportionate before recommending or implementing it. Use before GAN/adversarial review, after a review proposes added controls, or whenever a proposal adds repositories, services, approval layers, credentials, workflows, or ongoing operational burden.
---

# Proposal Proportionality Gate

Use this before committing to a material design. A red-team finding is evidence of a risk, not automatic authority to expand the product or operating model.

## Required sequence

1. State the decision, user outcome, deadline, affected environments, and irreversible or recurring cost.
2. State the threat model: trusted actors, untrusted actors, realistic failure modes, and explicit out-of-scope risks.
3. Classify every proposed control as **necessary now**, **optional hardening**, or **deferred**. Tie each necessary control to a concrete failure path and acceptance test.
4. Compare the smallest viable option with any heavier option. Include user-operation cost, implementation cost, and residual risk.
5. Recommend the smallest option that closes the in-scope risks. Do not implement a heavier option without an explicit user decision.
6. Only then run GAN or another adversarial review. Give reviewers the stated threat model and ask them to invalidate the selected option; do not silently upgrade the threat model to make a review pass.
7. If a review finds a new risk, decide whether it is in scope before changing the plan. Record it as optional hardening when the risk is real but disproportionate.

## Mandatory guardrails

- Never turn “GAN No-Go” into the objective. The objective is the user outcome with proportionate risk.
- Never add a repository, external service, approval layer, secret, signing system, or persistent operational role merely to eliminate a theoretical finding.
- Do not replace an approved plan or start implementation solely because a reviewer suggested a stronger architecture. First present the impact and obtain a decision when scope, cost, authority, or user workflow changes.
- Prefer same-repository branch protection, review, least privilege, dedicated workflow, feature flags, and reversible controls before cross-repository trust systems.
- Keep a stronger design as a clearly labelled future option when it protects against an out-of-scope adversary.

## Output before recommendation

Return these five short items:

1. **Decision and threat model**
2. **Necessary now** — each item with failure path and test
3. **Deferred hardening** — risk and trigger to revisit
4. **Recommended smallest design** — why it is sufficient
5. **User decision needed** — only if options materially differ

## Boundary example

For a Dev-only release workflow operated by trusted maintainers, a protected branch, required review, a dedicated Phase-only workflow, environment approval, and fail-closed receipts are normally the smallest design. A separate controller repository, independent signing hierarchy, or proxy is deferred unless the user explicitly needs protection from a compromised approved maintainer or has a comparable compliance requirement.
