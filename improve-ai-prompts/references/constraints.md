# Constraints (read the cited source before relying on this file)

This file summarizes invariants as of the `feat/shared-ai-evals-phase1` branch
(based on `origin/integrate/release-20260801`). Code moves faster than this
file. Re-read the cited source when a constraint looks stale or when the
behavior you observe disagrees with what is written here.

## `[feature:xxx]` tag — three live dependents

Every system prompt in `v2/src/lib/ai/prompts.ts` starts with a
`[feature:xxx]` line (e.g. `[feature:monthly-report]`). Three things parse it
at runtime. Removing or renaming it breaks all three simultaneously:

1. **Mock provider key** — `v2/src/lib/ai/mock.ts` `keyOf()` extracts the tag
   with `req.system.match(/\[feature:([a-z-]+)\]/)`. `registerMockResponse`
   calls in tests key off this same string. If the tag is missing, mock
   responses resolve to `"default"` and registered fixtures silently stop
   matching.
2. **Model resolution** — `v2/src/lib/ai/openai.ts` `modelFor()` reads the tag
   to look up `OPENAI_MODEL_<FEATURE>` (e.g. `OPENAI_MODEL_MONTHLY_REPORT`)
   before falling back to `OPENAI_MODEL` then the hardcoded default. A missing
   or malformed tag silently skips the per-feature override.
3. **`text.format.name` for Structured Outputs** — `structuredFormatName()` in
   the same file uses the tag as the JSON-schema format name sent to OpenAI.
   A missing tag falls back to the literal string `"structured-output"`,
   which still works but loses the ability to distinguish features in
   OpenAI-side logs/metrics.

The tag regex is `[a-z-]+` — lowercase letters and hyphens only. Do not use
underscores or camelCase in a feature key.

## Length / format constraints are not enforced by the JSON Schema sent to OpenAI

`v2/src/lib/ai/json-schema.ts` converts each zod schema to OpenAI's
`json_schema` strict-mode format and **strips** these keywords before
sending, because strict mode / compatible proxies reject them:

```
minLength, maxLength, pattern, format, minimum, maximum,
exclusiveMinimum, exclusiveMaximum, multipleOf, minItems, maxItems,
uniqueItems, default, $schema
```

So `monthlyReportSchema`'s `oneLiner.max(60)` and `detail.max(600)` are
enforced by zod **after** the response comes back, not by the API during
generation. If you want the model to actually aim for 60 chars / 200–300
chars, you must say so in the system prompt or in a schema field
`description` (descriptions survive the strip — only the listed keywords are
removed). Do not assume adding a `.max()` to a zod schema changes model
behavior; it only changes what gets rejected after the fact.

Structural constraints (object shape, required fields, enums, nesting) are
sent as-is and are enforced by OpenAI itself; only the keyword list above is
dropped.

## verify-numbers.ts strips whole sentences, not numbers

`v2/src/lib/ai/verify-numbers.ts`:

- `allowedNumbers(...inputs)` builds the set of numbers (normalized: full-width
  digits → half-width, comma separators removed) that appeared in the
  **user prompt** sent to the model.
- `stripUnverifiedNumbers(text, allowed)` splits `text` on
  `。`/`!`/`?`/`！`/`？`/newline and drops any **whole sentence** containing a
  number not in `allowed`. It does not redact just the number — the entire
  sentence is removed. A field can end up empty string; nothing re-generates
  it.

In `v2/src/app/api/ai/monthly-report/route.ts`, exactly **4 string fields**
go through this: `oneLiner`, `detail`, and per-project `basis` / `nextBasis`.
`nextGoal` and `nextActions` are explicitly **not** verified — they describe
a future the model is proposing, not a fact it is reporting, so there is
nothing in the input to check them against.

When writing golden cases or debugging "AI output looks empty/truncated",
check whether a sentence contains a stray number (a phone count, an
irrelevant date, a percentage) that isn't literally present in the
constructed user prompt — that sentence disappears silently.

## Prompt anatomy: `COMMON` affects every feature

`v2/src/lib/ai/prompts.ts` defines a `COMMON` string prepended to every
feature's system prompt (after the `[feature:xxx]` tag). Changing `COMMON`
changes behavior for all 6 features simultaneously (`milestone-gen`,
`plan-gen`, `monthly-report`, `expense-ocr`, `expense-check`, `vision-coach`).
There is no `goal-refine` feature in `AI_FEATURES`/`PROMPTS` yet as of this
writing (it appears only in a planning doc,
`v2/docs/24_plan_setup_improvements_2026-08-01.md`) — do not assume it exists
without checking `prompts.ts` first.

Before editing `COMMON`, run (or plan to run) evals for every feature that
has golden cases, not just the one you're focused on. Phase 1 of the eval
harness only has `monthly-report` cases; a `COMMON` change is exactly the
kind of "1 variable at a time" rule this Skill's step 4 exists to protect —
if you must touch `COMMON`, treat it as its own isolated change with its own
before/after eval run, not bundled with a feature-specific prompt edit.

## Input truncation: 8,000 chars, from the front

`v2/src/lib/ai/types.ts` `truncateForAI(text, limit = 8000)`: if `text` is
longer than 8,000 chars, it keeps `text.slice(0, 8000)` and appends
`\n…(以下省略)`. This keeps the **beginning** and drops the **end** of the
string. `v2/docs/10_ai_features.md` describes this as "超過分は古い記録から
落とす" (drop the old records), but the actual implementation is a blind
character-count slice — it does not know which part of the string is "old"
vs "new". Whether the end of the string is the newest or oldest record
depends entirely on the order `buildUserPrompt`/`buildMonthlyReportUserPrompt`
concatenates records in (currently chronological ascending: oldest logs
first, so truncation drops the **most recent** entries, which can remove the
evidence a result judgment most needs). `evals/golden/monthly-report/case-05-long-logs.json`
exercises this; check whether a fix belongs in prompt/ordering logic before
assuming eval regressions here are a prompt-wording problem.

`generate.ts`'s `generateStructured` wrapper always calls `truncateForAI`
before forwarding `user` to the provider — you cannot bypass truncation by
calling a provider directly except in eval/test code that intentionally
wants to inspect pre-truncation length (as `scripts/eval-prompts.ts` does).

## maxTokens defaults

`v2/src/lib/ai/openai.ts`: default `max_output_tokens` is **1,200**.
`v2/src/app/api/ai/monthly-report/route.ts` explicitly overrides it to
**2,000** because monthly-report returns 4 things in one call. If you add a
new field to `monthlyReportSchema` or make an existing field longer, check
whether 2,000 is still enough — `generateStructured` does not detect
truncated-due-to-token-limit vs. truncated-due-to-model-choice; both surface
as `OpenAIProviderError("incomplete", …)`.

## Retry behavior: at most 1 retry, only for structure failures

`openaiProvider.generateStructured` (`v2/src/lib/ai/openai.ts`) calls the
model once; if the response fails `JSON.parse` or `schema.parse`, it retries
**exactly once** with an appended instruction to re-emit valid JSON in the
same schema. If the retry also fails to parse, it throws
`OpenAIProviderError("invalid_output", …)` with a `zodIssues` summary (paths
+ codes only, never values). Upstream HTTP failures (401/429/5xx/timeout/
network) are **not** retried by this function — they propagate immediately
with their own category. `scripts/eval-prompts.ts` measures "one-shot pass"
(layer A) by counting `fetch` calls per `generateStructured` invocation: 1
call means no retry fired, 2 means it did.

## Rate limit

`v2/src/lib/ai/rate-limit.ts`: 10 calls per user per rolling 60-second window
(`consumeAiQuota`). Applies to every synchronous `/api/ai/*` route via
`enforceAiRateLimit`, checked **after** auth, **before** the provider call.
The eval runner bypasses this by calling `openaiProvider.generateStructured`
directly (not through an API route), so it does not exercise this limiter —
if you're specifically testing rate-limit UX, that must be done through the
route, not through `eval:prompts`.

## Job-queue features vs. synchronous features

As of the 2026-08-01 common job platform note in
`v2/docs/10_ai_features.md` §"共通ジョブ基盤", all 6 features are registered
under a shared `AiFeature` job registry (`ai_jobs` / `ai_job_events`,
`v2/src/lib/db/repositories/types.ts`), but only `expense-ocr`'s stored-draft
flow, `expense-check`, and the standalone OCR screen have actually migrated
their **UI** to the job client as of this writing. `plan-gen`/`milestone-gen`,
`monthly-report`, and `vision-coach` still call their `/api/ai/*` route
synchronously from the screen (see e.g.
`v2/src/app/api/ai/monthly-report/route.ts`, which calls
`generateStructured` inline inside the request handler and returns the
result in the same response). `scripts/eval-prompts.ts` calls
`openaiProvider.generateStructured` directly — it does not go through either
the synchronous route or the job queue, so job-queue-specific failure modes
(claim, requeue, background priority) are out of scope for this eval harness
entirely.

## Where the ground truth actually lives

- Prompts: `v2/src/lib/ai/prompts.ts`
- Output schemas: `v2/src/lib/ai/schemas.ts`
- Provider call + retry + Structured Outputs schema conversion:
  `v2/src/lib/ai/openai.ts`, `v2/src/lib/ai/json-schema.ts`
- Common wrapper (truncation, error normalization): `v2/src/lib/ai/generate.ts`
- Hallucination guard: `v2/src/lib/ai/verify-numbers.ts`
- Feature design doc (may lag code — verify before trusting):
  `v2/docs/10_ai_features.md`
- Eval runner (Phase 1, this Skill's mandatory before/after tool):
  `v2/scripts/eval-prompts.ts`, `v2/evals/golden/<feature>/*.json`
