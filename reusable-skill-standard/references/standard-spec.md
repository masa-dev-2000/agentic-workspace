# Reusable AI Skill standard

## Definition

A Skill is a versioned operational contract that combines trigger conditions, procedural guidance, structured inputs and outputs, bounded tools, authority policy, model policy, verification, failure handling, and audit evidence.

## Distinctions

- Prompt: an instruction or context fragment; it has no authority, lifecycle, or evidence contract by itself.
- Tool: a capability adapter that performs or proposes an operation; it is never trusted solely because a Skill names it.
- Agent: an execution principal with bounded capabilities and an assigned model; it cannot grant authority or approve itself.
- Skill: the reusable procedure and policy that determines when and how prompts, tools, and Agents may be combined.

## Lifecycle

1. Draft: author metadata, procedure, schemas, policy, and tests.
2. Validate: check structure, schema, permissions, references, and injection cases.
3. Review: independent review of safety, feasibility, and evidence.
4. Release: assign an immutable version and content digest.
5. Execute: resolve trigger, context, policy, model, tools, and verification.
6. Observe: store body-free audit and outcome evidence.
7. Improve: create a proposal or experiment; never mutate policy directly from a learning signal.
8. Deprecate: stop new invocations while keeping migration and audit references.

## Execution conditions

Run only when all trigger conditions match, exclusions do not match, required context is available, the requested tools are registered, and Gatekeeper grants the requested authority. Unknown classifications fail closed.

## Versioning

Use SemVer for the Skill package. Increment major for trigger, permission, output, or compatibility changes; minor for backward-compatible capabilities; patch for documentation or bug fixes. Pin `skill_id`, version, contract digest, policy revision, and model policy revision in every invocation.

## Testing

Each Skill must have schema validation, normal cases, boundary cases, permission tests, provider failure tests, replay/idempotency tests where applicable, and prompt-injection tests. A successful response is not completion evidence without output validation and accepted evidence.

## Audit

Audit the actor, Skill identity, target project, context references, policy decision, selected Agent/model, tools, proposed operation, approval, executed operation, verification, errors, retries, rollback, latency, and authoritative usage. Do not store prompt, response, tool body, credential, or raw secret data in the default audit ledger.
