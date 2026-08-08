---
name: research-alignment-deep-research
description: Plan and execute rigorous web-based domain research by first clarifying the research question, depth, entities, metrics, evidence standards, and deliverables with the user; then build a dependency roadmap, investigate primary sources entity-by-entity, maintain claim/source registers, and refuse to mark the work complete until depth and coverage gates pass. Use when a user asks for deep research, industry mapping, company/OEM analysis, market sizing, detailed parts/process knowledge, or a research roadmap.
---

# Research Alignment Deep Research

Use this skill to prevent shallow research. The first deliverable is an agreed research contract and roadmap, not a fast list of links. The workflow must distinguish -not publicly disclosed,- -not yet searched,- -estimated,- and -not applicable.-

## Non-negotiable principles

- Clarify scope and success criteria before broad searching. If the user asks to start immediately, make a compact explicit assumption block and ask for correction while continuing only with low-risk discovery.
- Treat a company, brand, product model, factory, component supplier, and distributor as different entity types.
- Prefer primary sources: regulatory filings, company filings, official product/technical pages, factory disclosures, patents, standards, and trade-association statistics. Use secondary sources for discovery and triangulation, not as sole proof of decisive claims.
- Record every material claim with source URL, publisher, publication/period, retrieval date, evidence class, confidence, scope, and time validity.
- Never convert missing public data into zero. Never convert a marketing superlative into a quantified market share.
- Separate design owner, brand owner, manufacturer, OEM contractor, component supplier, distributor, and repair provider.
- A roadmap is complete only when its acceptance tests pass; a visually complete dashboard is not evidence that the research is deep.
- Separate discovery from proof. Search results, snippets, AI summaries, and copied secondary claims may locate candidates but never count as evidence.
- Treat source independence as a data property. Multiple outlets repeating one release or dataset are one source family, not independent corroboration.
- For decisive claims, require either one Tier-1 source plus a passed contradiction search or two independent source families. Record exceptions and lower conclusion strength.

## Phase 0 - Research contract and alignment

Before building the roadmap, produce a one-page contract and ask the user to confirm or amend it. Include:

1. **Decision/use case** - what the resulting model will decide, predict, compare, or support.
2. **Object boundary** - included and excluded product tiers, geographies, time range, and adjacent domains.
3. **Entity grain** - legal entity vs brand vs product model vs facility vs component.
4. **Required metrics** - revenue, shipments, sales, production, capacity, employees, prices, market share; define units and periods.
5. **Required relationship types** - OEM, co-development, private label, component supply, bundle, compatibility, distribution, ownership.
6. **Required technical depth** - part-level BOM, material, process, equipment, tolerances, inspection, sub-suppliers, and known confidential boundaries.
7. **Evidence policy** - primary-only for final claims, or primary plus qualified secondary; acceptable estimate methods; citation format.
8. **Coverage target** - named core entities, candidate list, geography, and minimum source/claim counts per entity.
9. **Deliverables and stopping rule** - report, normalized datasets, glossary, ER/schema, roadmap, unresolved-question log.

If the user does not answer, label assumptions as `ASSUMED`, do not silently narrow the scope, and keep an explicit `open_decisions` list.

## Phase 1 - Depth rubric and roadmap

Use the depth rubric in [references/depth-rubric.md](references/depth-rubric.md). Choose a target depth per entity before searching. Build a dependency roadmap with 6-10 nodes, for example:

- R0 contract and terminology
- R1 entity universe and identity resolution
- R2 market and company metrics
- R3 product/model catalog
- R4 OEM, ownership, distribution, and compatibility graph
- R5 BOM, materials, processes, equipment, and supplier chain
- R6 claims/source normalization
- R7 domain model and schema
- R8 contradiction, coverage, and visual QA

Each node must have: entry criteria, outputs, evidence threshold, blocking dependencies, acceptance tests, and a status. Use one bottleneck only. Update the roadmap after each gate, with counts and a short evidence note. Do not mark parallel work complete merely because a file exists.

## Phase 2 - Discovery without premature conclusions

Build an entity inventory from official sites, filings, trade associations, catalogs, patents, standards, and specialist publications. For each entity, capture aliases, legal name, country, domain, role hypotheses, current/historical status, and candidate source URLs. Deduplicate by legal identifier, address, official domain, and ownership history. Keep candidates separate from confirmed manufacturers.

Use web search in batches, but open and read the strongest source. For technical questions, rely on primary documentation. Search for counterevidence and negative evidence (discontinued, outsourced, private-label, or -manufactured for-). Do not infer OEM from a product being bundled or looking similar.

### Phase 2A - Source verification gate

Apply [references/source-verification-protocol.md](references/source-verification-protocol.md) to every material claim. Keep a claim in `candidate` state until the source is opened, the relevant passage/table/page is captured, scope and dates are checked, and a contradiction search is recorded. A claim may become `supported`, `contradicted`, or `unresolved`; never promote it from a search snippet alone.

## Phase 3 - Entity-by-entity deep dive

For every core entity, complete a research card before counting it as covered:

- identity and ownership history;
- products and model families, with launch/discontinuation periods;
- role in the value chain;
- facilities and workforce scope;
- revenue, units, capacity, prices, and the exact denominator/period;
- product-level manufacturer/design/brand/distributor mapping;
- component-level BOM with materials, quantities where public, process, inspection, and supplier;
- OEM/private-label/co-development/compatibility evidence;
- source-backed glossary terms and model-specific terminology;
- unresolved questions and what was searched.

For high-priority entities, require at least one source in each applicable evidence lane: corporate/financial, product/catalog, manufacturing/technical, and relationship/supply-chain. If a lane has no public source, record `reviewed_absence` with the search scope; do not leave a silent blank.

## Phase 4 - Metrics and estimation discipline

Store revenue, capacity, production, shipments, sales, and inventory as different metrics. Store company-total and segment-level values separately. Preserve currency, tax/wholesale/retail basis, units, period, geography, and whether the value is reported, historical, estimated, unquantified claim, or not disclosed.

An estimate is allowed only when the formula, inputs, assumptions, range, and sensitivity are explicit. A proxy such as record sales may provide demand context but cannot be converted into stylus units without a defensible conversion model. When conversion is not defensible, use `not_estimable`.

## Phase 5 - Normalize claims, sources, and uncertainty

Create `source-register`, `claim-register`, `metric-register`, `relationship-register`, `bom-process-register`, and `glossary` artifacts. Use atomic claims: one subject, predicate, object, scope, and period per row. Track support and contradiction separately. Attach confidence to evidence strength, not truth probability. Keep historical and current claims as separate validity intervals.

For each material claim, store `source_tier`, `source_family_id`, `publisher`, `underlying_dataset`, `quoted_location`, `publication_date`, `retrieved_at`, `valid_from`, `valid_to`, `freshness_ttl`, `evidence_grade`, `conclusion_strength`, `contradiction_queries`, and `search_scope`. Keep evidence grade separate from conclusion strength.

## Phase 6 - Model and QA

Design an ER model that supports legal entities, brands, facilities, products, generator platforms, components, component usage, process steps, relationships, compatibility, claims, metrics, estimates, terms, and sources. Enforce foreign keys, valid periods, unique IDs, and controlled vocabularies.

Before completion, run these gates:

- every core entity has a research card and minimum evidence lanes;
- every decisive claim has a source or an explicit reviewed-absence record;
- every decisive claim passed the source-verification gate, with an evidence grade, conclusion strength, source-family independence check, and contradiction-search record;
- no duplicate IDs or orphan references;
- current vs historical values are separated;
- manufacturer vs brand vs distributor is not conflated;
- OEM and compatibility labels are source-supported;
- unexplained contradictions are listed;
- unresolved candidates and confidential boundaries are visible;
- stale claims and their re-verification triggers are visible;
- roadmap acceptance tests pass;
- any roadmap HTML/dashboard is checked at common viewport sizes and has no clipping or overflow.

## Completion rule and handoff

Report the result as one of `COMPLETE`, `COMPLETE_WITH_LIMITS`, or `BLOCKED`. Use `COMPLETE` only when all agreed depth gates pass. Use `COMPLETE_WITH_LIMITS` when the public-data boundary is fully documented but some requested values are not disclosed. Never claim -all companies- when the population is an evidence-backed lower bound or candidate universe.

Handoff must include: the contract and assumptions, roadmap status, executive findings, per-entity coverage matrix, unresolved questions, evidence limitations, links to normalized artifacts, and a recommended next research tranche.

## Recovery from shallow or failed research

If an intermediate result has only broad industry summaries, few entity-specific sources, no negative-evidence log, or no metric denominator, stop expanding the report. Re-open the contract, lower the unit of analysis to entity/product/component, add the missing evidence lanes, and move the roadmap bottleneck backward. Do not polish a shallow result into a -complete- dashboard.

