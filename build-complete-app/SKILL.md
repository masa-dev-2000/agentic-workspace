---
name: build-complete-app
description: Turn a short product request into a verified local Web MVP for a new or existing project, coordinating requirements, reuse search, implementation, tests, and real-browser UX validation with minimal user questions. Use when the user asks to build an app, prototype, working web product, or a Google AI Studio-like end-to-end result from sparse instructions.
---

# Build Complete App

Produce a working local Web MVP, not merely a scaffold or plan. Minimize user decisions while preserving consequential approvals.

Read [app-contract.md](references/app-contract.md), [mvp-definition.md](references/mvp-definition.md), and [stack-routing.md](references/stack-routing.md) before implementation.

## Defaults

- New project: `C:\Users\masa\dev\<slug>` unless the user gives a destination.
- Existing project: preserve its stack and conventions when viable.
- New Web stack: React, TypeScript, Vite, npm.
- Backend: add Supabase only when persisted multi-user data, authentication, storage, realtime, or server-side policy is actually needed.
- Delivery: runnable local MVP. Do not deploy to production unless separately requested and approved.
- Reuse: search the private audited catalog before implementing common infrastructure. Never bypass `harvest-components` rights gates.

## Workflow

1. Use `project-orchestrator` as the single project-management authority. Inspect the repository and persistent ledger; do not create a competing task system.
2. Convert the request and repository evidence into `app-contract.json`. Infer reversible details. Ask only when an unresolved choice materially changes users, data, cost, permissions, public behavior, or irreversible work.
3. Generate likely personas and their primary journey. Define one outcome-oriented vertical slice and explicit non-goals.
4. Validate the contract:

   ```powershell
   node scripts/app-contract.mjs validate --file <project>\app-contract.json
   ```

5. Search the reuse catalog:

   ```powershell
   node scripts/component-search.mjs "<capability>"
   ```

   Use only audited entries. Prefer an existing dependency over harvested code when it is simpler and license-compatible.
6. Implement the thinnest end-to-end vertical slice: real input, core behavior, persistence only if required, success/error/empty/loading states, and a clear completion path.
7. Apply the workspace UX principles. Keep primary information and actions within each target viewport; use progressive disclosure instead of shrinking text or creating body scroll.
8. Verify behavior with tests and a production build. Then inspect the actual rendered app in a browser at target desktop and mobile viewports. Capture images and check body scroll, missing content, overlaps, tap targets, readability, empty/error/loading states, and the primary journey.
9. Run:

   ```powershell
   node scripts/mvp-check.mjs --project <project> --run
   ```

   Fix failures with materially changed attempts. After three failed attempts with the same signature, stop the loop, preserve evidence, and surface the blocker.
10. Have `progress-verifier` verify observable acceptance criteria. Only verified completion may trigger `harvest-components` candidate detection for an already authorized project.

## Existing projects

- Inspect `AGENTS.md`, package manager, scripts, architecture, dirty changes, and current tests first.
- Do not replace the framework or rewrite working structure without a demonstrated requirement.
- Keep user changes intact and scope patches to the requested outcome.

## Completion report

Lead with the runnable outcome. Include:

- project path and start command;
- implemented user journey;
- build/test/browser evidence;
- known MVP limitations;
- pending consequential decision, if any;
- reuse package used or harvest candidate created.

Do not call a scaffold, unchecked mock, passing plan, or self-report a completed MVP.

