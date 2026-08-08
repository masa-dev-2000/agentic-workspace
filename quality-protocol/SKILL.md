---
name: quality-protocol
description: Legacy explicit-only compatibility entry for the former gated implementation protocol. Use only when the user explicitly invokes $quality-protocol; normal implementation quality is governed by AGENTS.md, the active Skill completion contract, executed verification, and independent review.
---

# Quality Protocol

> Compatibility period: do not activate this Skill implicitly. Prefer the canonical AGENTS.md
> rules and the active Skill's completion contract.

This protocol turns the guidelines in the global CLAUDE.md into executable gates with pass conditions. It exists to buy back single-pass judgment with multiple explicit passes: do NOT trust your first pass.

Every gate is mandatory. Skipping a gate is allowed only with a one-sentence justification stated in your response (escape valve for trivial tasks).

## Gate A — Restate before coding

Before writing any code, state briefly in your response:

1. Your interpretation of the request
2. Assumptions you are making
3. The files you will touch and why

Pass condition: no assumption is a guess about user intent. If one is, ask the user instead of guessing.

## Gate B — Reproduce-first for bugs

Never fix a bug you have not reproduced.

1. Write a failing test (or a minimal repro command) FIRST
2. Run it and watch it fail
3. Fix the bug
4. Run it and watch it pass

Pass condition: you have seen both the failure and the pass in this session. If reproduction is impossible, state why and what you verified instead.

## Gate C — Adversarial review

After implementing, launch the `adversarial-reviewer` agent (it fetches the diff itself via `git diff`). For each finding, apply the remediation path and add the regression test the reviewer named (or state why the test is not warranted). Then launch the agent again.

Exit condition: zero INTRODUCED findings of severity medium or higher (the reviewer states PASS), OR 3 rounds completed (report remaining findings honestly in that case). Pre-existing defects the reviewer notes are surfaced to the user, not fixed unprompted.

If the agent is unavailable, self-review the full diff against the same checklist (see the agent definition), acting as a reviewer who wants to reject the change.

Note: in projects using the dev-flow plugin, this gate is the pre-PR self-review; the dev-flow review session remains the post-PR approval review.

## Gate D — Verified means executed

"Done" requires evidence produced in THIS session: a test run, build, or actual execution, with the real output.

- If verification failed or was skipped, report exactly that. Never smooth it over.
- "It should work" is a Gate C failure finding, not a completion report.

## Gate E — Final report

Structure the final message as:

1. First sentence: what happened / what you found
2. What you changed
3. What you verified, with actual results
4. What remains (if anything)

Write complete sentences. No fragment chains, no shorthand invented mid-task.
