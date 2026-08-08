# Deep research depth rubric

Use this rubric to agree on the target before searching. A score is not a substitute for evidence; it is a gate for what -deep enough- means.

## Levels

| Level | Scope | Minimum evidence and output |
|---|---|---|
| L0 orientation | vocabulary and boundaries | 5+ authoritative discovery sources; explicit exclusions |
| L1 landscape | entities and roles | entity inventory, identity resolution, candidate/confirmed split, 2 sources per core entity |
| L2 company intelligence | metrics and business relationships | research card per core entity; corporate, product, and relationship evidence; period/unit-aware metrics |
| L3 product intelligence | models and compatibility | model-family catalog; manufacturer/design/brand mapping; specifications and historical validity |
| L4 manufacturing intelligence | BOM and process | part-level BOM; materials; process steps; inspection; supplier and confidentiality boundaries |
| L5 supply-chain intelligence | sub-suppliers and OEM graph | product-scoped relationship graph; primary evidence for each decisive edge; contradictions and uncertainty |
| L6 decision-grade | model-ready, auditable research | all agreed gates pass; claim/source/metric registers; schema; reproducible QA; explicit public-data limits |

## Per-entity coverage matrix

Use columns: `entity`, `identity`, `products`, `financial`, `units/capacity`, `manufacturing`, `BOM`, `OEM/relationships`, `suppliers`, `terminology`, `negative_evidence`, `sources`, `confidence`, `open_questions`.

Mark each lane `verified`, `partial`, `reviewed_absence`, or `not_applicable`. A core entity cannot be `covered` when any applicable lane is blank. `reviewed_absence` is acceptable only if the search scope, dates, and attempted source classes are recorded.

## Evidence thresholds

- Core company at L2+: minimum 3 distinct source classes, including one official source.
- Core company at L4+: minimum 5 distinct source classes where applicable, including one manufacturing/technical source and one product source.
- Decisive OEM edge: direct manufacturer/customer statement, filing, contract disclosure, or two independent sources with no contradiction.
- Financial figure: exact period, currency, denominator, scope, and source; registry-transcribed figures are secondary unless the filing is directly available.
- Technical claim: model, material/process context, and source location; do not generalize from one model to a whole company without evidence.

## Stop conditions

Stop and renegotiate scope when the user-s requested universe is unbounded, when primary evidence is unavailable for a decisive claim, or when a -complete- result would require inventing conversion factors. Preserve the unresolved queue and propose the next tranche instead.

