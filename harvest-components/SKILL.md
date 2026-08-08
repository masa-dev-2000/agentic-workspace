---
name: harvest-components
description: Find technically reusable web components in explicitly authorized projects, preserve provenance and rights evidence, create behavior-only clean-room contracts, and register independently implemented packages after human approval. Use after verified project completion, when searching the private reuse catalog, or when the user asks to reuse or extract prior implementation.
---

# Harvest Components

Build a private component catalog without copying project-specific code, assets, data, wording, or business rules. Treat abstraction as an engineering technique, never as proof of legal safety.

## Non-negotiable gates

- Process only a project already authorized in `policies/projects.json`.
- Require a recorded rights basis and evidence reference. Never infer authorization from filesystem access, authorship, or a permissive-looking repository.
- Restrict candidates to generic technical kinds: UI, form, auth, CRUD, API client, validation, state, and layout.
- Block secrets, credentials, customer data, brand assets, product copy, proprietary schemas, distinctive algorithms, and domain-specific rules.
- Allow external dependencies only under the configured license allowlist. Send unknown, copyleft, or custom licenses to human/legal review.
- Require explicit human approval immediately before registration.
- Never describe an output as lawsuit-proof, legally guaranteed, or cleared by similarity alone.

Read [rights-policy.md](references/rights-policy.md) before authorization, approval, or license decisions. Read [behavior-contract.md](references/behavior-contract.md) before creating a clean-room contract.

## Commands

Run:

```powershell
node scripts/reuse.mjs status
node scripts/reuse.mjs search --query "validated form"
node scripts/reuse.mjs authorize --project <id> --path <path> --rights-basis personal-owned --evidence <reference>
node scripts/reuse.mjs scan --project <id> --kind form --files-json '["src/Form.tsx"]'
node scripts/reuse.mjs contract --candidate <id> --input <json-file>
node scripts/reuse.mjs approve --candidate <id> --package-dir <fresh-package> --kind form
node scripts/reuse.mjs audit
```

`authorize` and `approve` mutate the trust boundary. Execute them only after the user explicitly confirms the exact project/evidence or candidate/package.

## Workflow

1. Run `status` or `search` first. Reuse an existing audited package when it satisfies the need.
2. Confirm the source project is explicitly authorized. If not, prepare the exact `authorize` command and ask for approval; do not scan first.
3. Run `scan` against an explicit changed-file list when available. A verified-completion event may trigger this automatically, but it must not auto-register anything.
4. Have a **Source Analyst** inspect only the authorized source and produce a behavior contract. The contract may contain observable behavior, public interfaces, constraints, accessibility needs, edge cases, and tests. It must contain no source code, snippets, distinctive strings, assets, proprietary names, or source-derived internal structure.
5. Start a fresh **Implementation Agent** with access only to the behavior contract and ordinary public documentation. Do not provide the original source, source paths, diffs, or analyst scratch notes. If an independent agent cannot be created, leave the candidate in `waiting-clean-room`; do not simulate separation in one context.
6. Implement a standalone package and tests. Replace domain terms with generic fixtures.
7. Run the contamination scan through `approve`. If it fails, keep the block and redesign from the contract; do not weaken thresholds.
8. Show the candidate, behavior contract, provenance, license result, tests, and package destination to the user. Register only after explicit approval.
9. Run `audit` after registration. A failed audit means the component is unavailable for reuse.

## Project-manager integration

After `progress-verifier` records verified completion, the AI Project Manager may scan authorized changed files and add one review item to the existing human priority queue. The queue item must say what was found, why it appears reusable, its rights evidence, review effort, approval action, and what happens if deferred. Duplicate fingerprints must not create duplicate queue items.

## Output

Report:

- reused existing package, new candidate, blocked, or no useful candidate;
- exact evidence and license status;
- clean-room status;
- tests and audit result;
- the one next human decision, if any.
