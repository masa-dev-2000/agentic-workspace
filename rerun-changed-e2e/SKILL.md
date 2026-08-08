---
name: rerun-changed-e2e
description: Rerun evidence-backed end-to-end regression only for behavior that changed since a verified baseline. Use when a user asks to retest recent changes, repeat selected items from a prior E2E checklist, verify a patch or branch without redoing unaffected screens, or prove UI-to-HTTP-to-persistence behavior with screenshots and viewport checks.
---

# Rerun Changed E2E

Revalidate changed behavior without spending time on unchanged coverage. Treat prior evidence as a baseline, not as proof of the new source state.

## Workflow

1. Read applicable repository instructions and UX principles.
2. Record branch, HEAD, worktree status, target environment, and prohibited external actions.
3. Identify the last verified baseline from commits, prior evidence, test reports, or the user-provided checklist.
4. Diff the current source against that baseline. Build a change-to-journey matrix containing:
   - changed behavior;
   - affected role and screen;
   - entry path and prerequisites;
   - write/read API or server repository involved;
   - viewport and evidence required.
5. Exclude unchanged journeys unless they are a direct dependency or shared-shell regression risk. State every included dependency.
6. Partition independent journeys across subagents only when the user requests delegation or applicable instructions require it. Give each agent disjoint screens and forbid source edits during audit-only runs.
7. Run targeted automated tests first. Do not accept component tests with mocked fetch as E2E proof.
8. Exercise each selected journey in a real browser against a dedicated localhost instance:
   - start from the real entry point;
   - perform the user action rather than setting internal state;
   - cover success, validation, failure/retry, and reload when relevant;
   - inspect console and page errors;
   - take screenshots at the changed breakpoint and any shared-layout breakpoint at risk.
9. For writes, prove the full boundary:
   `UI action → HTTP route → authorization → repository → persistence → GET or page reload`.
   Use reversible synthetic data. Restore or delete it when safe.
10. Verify viewport metrics: body/html dimensions, horizontal overflow, clipped or overlapped fixed elements, and intended scroll regions. Reset temporary browser viewport overrides.
11. Run the smallest build/type/lint gate proportional to the changed files. Run broader gates only when shared code, schemas, auth, or release configuration changed.
12. Stop local servers and confirm the worktree has no unintended changes.

## Evidence standard

For every selected journey report:

| Field | Required evidence |
|---|---|
| Source reason | File or commit diff that caused retesting |
| Browser result | Exact action and visible result |
| Backend result | HTTP status and persistence/reload observation |
| Failure behavior | Visible recovery path or explicit not-applicable reason |
| Visual result | Viewport, screenshot, overflow/scroll result |
| Automation | Targeted test command and pass/fail count |

Do not call a journey PASS when only one layer was checked. Use:

- `PASS`: browser, backend, reload, and relevant visual evidence agree.
- `PARTIAL`: a required layer could not be exercised.
- `FAIL`: observed behavior contradicts the expected contract.
- `NOT RUN`: excluded with a concrete reason.

## Boundaries

- Do not fix defects unless the user separately authorizes implementation.
- Do not access production, secrets, external email, paid AI, storage, DNS, or deployment unless explicitly authorized.
- Do not substitute screenshots for interaction or mocked fetch tests for backend persistence.
- Do not repeat the full historical checklist when the user requested changed-only regression.
- Preserve dirty worktrees by using a dedicated worktree and dedicated localhost port.

## Handoff

Lead with the regression outcome. Include branch/HEAD, selected journeys and exclusion rationale, failures with reproduction steps, commands and counts, screenshots/viewports, backend persistence evidence, blockers, unintended changes, and whether any external or production action occurred.
