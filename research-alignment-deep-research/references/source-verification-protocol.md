# Source Verification Protocol

Use this protocol when a result must withstand scrutiny rather than merely look researched.

## Source tiers

- **Tier 1 — primary**: regulator filings, official filings, official technical/product documentation, standards, patents, court or government records, facility disclosures, and first-party datasets.
- **Tier 2 — qualified secondary**: audited research, trade-association statistics, methodology-disclosed analyst work, or reporting that links to and accurately quotes primary material.
- **Tier 3 — secondary**: reputable journalism, specialist publications, interviews, and market commentary.
- **Tier 4 — discovery only**: search-result pages, snippets, social posts, aggregators, unsourced blogs, and AI-generated summaries.

Tier 4 can create a candidate URL or query, but cannot support a claim. Tier 3 may support context or a qualified estimate when Tier 1/2 is unavailable; record the limitation and lower conclusion strength.

## Discovery → proof workflow

1. Search broadly to create candidates; label every candidate `discovery`.
2. Open the original source and identify the exact passage, table, figure, page, or data row.
3. Record publisher, parent organization, underlying dataset, publication/effective date, retrieval date, scope, geography, denominator, and validity period.
4. Assign a `source_family_id`; syndicated copies, press-release rewrites, and reports using the same dataset count as one family.
5. Run a finite contradiction search: use the claim's subject plus terms such as `discontinued`, `outsourced`, `denies`, `restated`, `not disclosed`, `different`, and the relevant date or model.
6. Set claim state to `supported`, `contradicted`, or `unresolved`. Do not use absence of search results as proof of absence; use `reviewed_absence` with the searched scope.

## Decisive-claim rule

A decisive claim needs either:

- one Tier-1 source and a completed contradiction search; or
- two genuinely independent source families with consistent scope and dates.

If neither is possible, state the claim as `indicative` or `unresolved`, record the reason, and use `COMPLETE_WITH_LIMITS` rather than silently upgrading it.

## Freshness and stopping

Assign a freshness TTL by domain: prices, availability, regulations, and product specs are short-lived; historical ownership and patents may be long-lived. Mark claims `stale` after TTL and define the event that triggers re-verification. Stop a search when the contract's depth/coverage target is met, marginal evidence value is low, the time/budget limit is reached, or three consecutive query families add no material evidence. Report the remaining uncertainty.

Do not bulk-copy copyrighted pages or send confidential material to external services. Respect access controls, robots policies, rate limits, and local-only handling requirements.
