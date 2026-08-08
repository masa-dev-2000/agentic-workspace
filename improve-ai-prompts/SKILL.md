---
name: improve-ai-prompts
description: Improve, tune, or debug the production AI feature prompts of kyoryokutai_support v2 (plan-gen, milestone-gen, monthly-report, expense-ocr, expense-check, vision-coach, goal-refine in v2/src/lib/ai/prompts.ts) with a mandatory before/after golden-dataset evaluation. Use when changing any system prompt, output schema wording or description, or user-prompt builder; when AI output quality is reported as wrong, unnatural, truncated, hallucinated, or stripped by verify-numbers; when adding a new AI feature prompt; or when running or extending offline prompt evals (npm run eval:prompts). Do not use for mock-only test changes or for prompts unrelated to this product.
---

# Improve AI Prompts

The eval runner lives in the repo, not in this Skill:
`v2/scripts/eval-prompts.ts` is the one true runner. It imports the real
prompts (`v2/src/lib/ai/prompts.ts`), the real schemas
(`v2/src/lib/ai/schemas.ts`), and the real provider
(`v2/src/lib/ai/openai.ts`) directly — the same code paths production uses.
Do not copy scoring logic, prompt text, or schema shape into this Skill.
A script here would drift from the provider/schema the first time either
changes, and you'd be scoring against a fiction. This Skill only tells you
when and how to call the repo's runner, and what the surrounding contract is.

## 1. Start from a reproduced failure

If you're here because output "seems wrong," don't touch the prompt yet.
Turn the bad example into a golden case first:

1. Take the real (or realistic) input that produced the bad output and
   rebuild it as **synthetic data** — invented municipality, invented member,
   invented numbers. Never copy a real user's project names, log text, or
   amounts into `v2/evals/golden/<feature>/`.
2. Add it as `v2/evals/golden/<feature>/case-NN-<short-name>.json` following
   the shape of existing cases (`{ id, description, input, expects: { rules,
   notes } }`) — match the DTO types in
   `v2/src/lib/db/repositories/types.ts` exactly for whichever feature you're
   testing.
3. Run it (`npm run eval:prompts -- --feature <f> --case case-NN-...`) and
   confirm it actually reproduces the bad behavior (fails a rule, or a human
   reading the raw output agrees it's bad). If it doesn't reproduce, you
   don't understand the bug yet — don't edit the prompt on a guess.

If you're here to add a new feature prompt or improve wording pre-emptively
(no reported failure), skip to step 2, but still add at least one golden
case for the feature before you're done (step 6).

## 2. Read the contract before editing

Read `references/constraints.md` in this Skill, then verify the specific
claim you're relying on against the cited source file — the reference file
can lag the code. The invariants that most often get broken by a prompt
change:

- The `[feature:xxx]` tag at the top of every system prompt in
  `v2/src/lib/ai/prompts.ts` is read at runtime by three different things
  (mock key, per-feature model override, Structured Outputs format name).
  Never remove or rename it as a "cleanup."
- Character-count and format constraints (60 chars, 200–300 chars, etc.) are
  **not** enforced by the JSON Schema OpenAI receives — `json-schema.ts`
  strips `minLength`/`maxLength`/`pattern`/etc. before sending. If you want
  the model to respect a length, it has to be written into the prompt text
  or a schema field `description`, not just a zod `.max()`.
- The monthly-report feature verifies exactly 4 fields against input numbers
  post-hoc (`oneLiner`, `detail`, `basis`, `nextBasis`) and silently deletes
  whole sentences that fail — `nextGoal`/`nextActions` are intentionally not
  verified.
- `COMMON` in `prompts.ts` is shared by all 6 (soon 7) features. A `COMMON`
  edit is its own isolated change with its own before/after eval run — never
  bundle it with a feature-specific prompt edit.
- User input is truncated to 8,000 chars from the **front** of the built
  string (see `references/constraints.md` for why this can drop the most
  recent, most decision-relevant records rather than the oldest ones).
- `maxTokens` defaults to 1,200; monthly-report overrides to 2,000 because it
  returns 4 things per call. Adding fields may require raising this.

## 3. Measure before

```
cd v2
npm run eval:prompts -- --feature <feature>
```

This calls the real OpenAI API (no mock fallback — the runner refuses to run
without `OPENAI_API_KEY`) and writes
`v2/evals/results/<ISO-timestamp>-<feature>.json` + `.md`. Note the layer A
(one-shot pass rate), layer B (zod pass rate), and layer C (rule metrics:
hallucination-removal rate, length-band compliance, result-match rate)
numbers as your baseline. If `v2/evals/golden/<feature>/` doesn't exist yet
(a feature beyond `monthly-report`), you need at least 3–5 cases covering the
normal path, an empty/zero-evidence path, and a path that should fail a rule
— see the `monthly-report` cases for the pattern — before you can measure
anything.

## 4. Change one variable at a time

Edit exactly one of: system prompt wording, a schema field's `description`,
or the user-prompt builder (`buildMonthlyReportUserPrompt` in
`v2/src/lib/ai/user-prompts.ts` once it exists — until then it's duplicated
inside `v2/src/app/api/ai/monthly-report/route.ts` and
`v2/scripts/eval-prompts.ts`; keep both in sync if you touch it). Do not
also rename the feature, change `maxTokens`, or touch `COMMON` in the same
change unless the task is specifically about one of those.

## 5. Measure after and gate the change

```
npm run eval:prompts -- --feature <feature>
```

Compare against the before scorecard. Do not ship if:

- Layer A (one-shot pass rate) or layer B (zod pass rate) dropped below
  baseline — a wording change that makes the model less likely to follow the
  schema is a regression even if the surviving outputs read better.
- Any layer C rule metric (hallucination-removal rate, length-band
  compliance, result-match rate) is worse than baseline on any case that
  passed before.
- A new golden case you added to reproduce the original bug still fails.

Record both scorecards (paths in `v2/evals/results/`) in
`v2/evals/baselines.md` — append a row, don't overwrite history. If it's
subjective which version reads better (this is common — rule metrics don't
capture "does this sound natural"), pull 3–5 before/after output pairs for
the same case and show them to a human directly rather than arguing from
aggregate scores alone.

## 6. Checklist before calling it done

- [ ] `[feature:xxx]` tag unchanged (or, if the change is a new feature,
      present and lowercase-with-hyphens)
- [ ] At least one golden case added or updated for the behavior you changed
- [ ] Before and after scorecards exist under `v2/evals/results/` and are
      recorded in `v2/evals/baselines.md`
- [ ] Gate in step 5 passed (no metric regression; reproduced bug now fixed)
- [ ] `v2/src/lib/ai/mock.ts` fixtures / test `registerMockResponse` calls
      still match the schema if you changed schema shape
- [ ] `cd v2 && npm test` green (mock-provider tests must stay deterministic
      and must not call the real API)
- [ ] `npm run lint` / `npm run typecheck` green
- [ ] `v2/docs/10_ai_features.md` updated if the feature's documented
      behavior (inputs, outputs, constraints) actually changed
- [ ] `v2/docs/07_work_log.md` entry recorded (what changed, before/after
      scores, why)

## 7. What this Skill does not do

- Does not add real API-key evals to CI. `npm run eval:prompts` stays a
  manual, human-invoked step — CI keeps using the mock provider.
- Does not put real users' project names, log text, or amounts into golden
  cases, ever. Synthetic only.
- Does not ship a prompt change without a completed before/after eval run —
  "should read better now" is not a gate.
- Does not restructure a prompt based on a single judge score once Phase 2
  (LLM-as-judge, see `v2/evals/judges/monthly-report.md`) exists. A judge
  score is one input among the layer A/B/C rule metrics and human review of
  actual output pairs, not a standalone authority.
