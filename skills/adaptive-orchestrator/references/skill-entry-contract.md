# Local Skill Entry Contract

The Registry is the source of truth for each Skill's single primary responsibility,
entrypoint, invocation policy, delegation edges, and state owner.

## Entry precedence

1. Explicit Skill request
2. Human authority or approval requirement
3. Project ledger or project identifier
4. Plan-only request
5. Explicit adversarial review
6. Non-trivial execution through `adaptive-orchestrator`
7. Trivial direct response

The router is deterministic, records the selected entrypoint and reason, rejects unknown
Skills, rejects delegation cycles, and limits route depth to eight. Registry metadata never
grants authority; the policy gate remains authoritative.

## Ownership

`adaptive-orchestrator` owns runtime stage state, `project-orchestrator` owns PM ledger
state, `planning` owns plan artifacts, `gan` owns review artifacts, and
`agent-team-orchestrator` owns agent leases. Other Skills return artifacts and evidence
without taking ownership of another Skill's state.

## Migration

The new fields are additive to the existing Registry contract. Existing Skill keys and
explicit invocations remain valid. Deprecated Skills are excluded from automatic selection
but remain available through an explicit compatibility shim until separately retired.
