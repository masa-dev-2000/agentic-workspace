# Dependency investigation

Build the execution DAG from evidence before rendering it.

## Evidence passes

1. Read roadmap, phase requirements, TODOs, ADRs, issues, and acceptance criteria.
2. Inspect build manifests, imports, service calls, schemas, migrations, workflows, and tests.
3. Query an available code graph for symbol-, module-, call-, and impact-level relationships.
4. Inspect operational dependencies: credentials, environments, data migration, deployment,
   observability, rollback, and restore.
5. Inspect authority dependencies: decisions, approvals, human-only work, vendors, and external
   services.
6. Challenge every proposed edge: ask whether the downstream outcome can start or finish without
   the upstream outcome. Remove mere chronological ordering.
7. Record evidence per edge using `dependencyEvidence`. Mark missing evidence explicitly.
8. Detect missing references, cycles, disconnected release gates, and critical-path ambiguity.

## Formal human decisions

Classify every formal human decision before creating its roadmap node:

- `evidence_interpretation`: inspect source artifacts, neighboring records, validators, and relevant public primary sources before asking;
- `internal_business_rule`: collect local cross-source evidence and search relevant public primary sources, but keep the authorized human as the final authority;
- `external_fact`: require an authoritative public or contractual source, or explicitly record that no independent corroboration was found;
- `visual_review`: package the source image, raw value, locator, and diff; public web research is normally not applicable;
- `authorization_or_acceptance`: verify authority and artifact readiness; do not pretend that web research can replace approval.

Record the strongest hypothesis, alternatives, contradictions, corroboration status, and exact residual question. “No relevant public source found” is a useful result and must not be rewritten as corroboration. An unanswered question is `waiting` unless a required downstream outcome cannot proceed safely; only that latter case is `blocked`.

## Code graph boundary

CodeGraph-style tools are evidence providers for structural code relationships such as imports,
calls, implementations, symbol references, and impact paths. They do not normally prove product
sequence, human approval, external-service readiness, migration safety, or release gates.

Combine code-graph evidence with project and operational evidence. Do not translate every import
into a roadmap edge; aggregate code relationships into outcome-level dependencies.

## Completion rule

Do not call dependency investigation complete until:

- every rendered edge has evidence or is visibly marked unverified;
- cycles and missing task references are resolved or surfaced;
- critical and release paths include non-code dependencies;
- dependency-evidence coverage is displayed.
