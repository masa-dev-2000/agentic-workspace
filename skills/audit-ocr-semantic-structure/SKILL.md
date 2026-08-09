---
name: audit-ocr-semantic-structure
description: Audit OCR page outputs for completeness, physical-format drift, column/value mismatch, identifiers, multivalue cells, revisions, ditto marks, relations, and normalization risks while preserving raw evidence. Use when OCR pages, tables, ledgers, catalogs, BOMs, work instructions, warning assessments, relation sidecars, or human-review packages must be inspected, regrouped, validated, or prepared for evidence-backed human decisions.
---

# Audit OCR Semantic Structure

Preserve observation separately from interpretation. Never turn a difficult page into a page-wide exception when physical blocks remain observable.

## Workflow

1. Locate the job manifest, page images, stored results, warning assessments, relation sidecars, attempts, and validators.
2. Verify provenance and enumerate every page before semantic analysis.
3. For each page, require stored result, warning assessment, relation sidecar, and validator evidence. Treat missing artifacts as pipeline failures, not semantic uncertainty.
4. Preserve readable raw text. Mark only unreadable characters or cells `illegible`; mark only genuine competing readings `ambiguous`.
5. Preserve cancellation, overwrite, ditto, arrows, brackets, and relation lines without guessing their targets.
6. Classify physical formats before comparing values. Do not assume `template_id` uniquely identifies a layout.
7. Audit columns, values, identifiers, row grain, multivalue cells, history, relations, and normalization boundaries.
8. Produce human-review items at cell, relation, format, or decision-cluster granularity—not page granularity.
9. Show the original image for every human question. State where to look, the observed raw value, a strongest hypothesis, alternatives, evidence, and a falsification condition.
10. Ask one consequential question at a time.
11. Keep OCR originals immutable. Store proposed interpretations and human edits in separate artifacts.
12. Run structural, provenance, deterministic, package, and visual validators before reporting completion.

## Physical format grouping

Create a `format_id` from observable layout features:

- physical column count and order;
- header/field sequence;
- record type;
- left/right panel or spread structure;
- header generation and aliases;
- source/image provenance;
- distinct row signatures inside the page.

Use `template_id` only as a coarse OCR routing label. Split pages with multiple row signatures into block-level format membership. Also detect the inverse problem: identical physical formats divided among different template IDs.

## Semantic audit checks

Inspect at least:

- systematic blank columns beside populated columns;
- one-column or multi-column shifts;
- field names whose value types do not match;
- multiple physical values collapsed into one cell;
- multiline values that may represent sets, notes, corrections, or linked records;
- repeated identifiers with conflicting attributes;
- exact duplicate rows that may be real duplicates, ditto expansion, continuation, or double transcription;
- unit, decimal, range, symbol, price, JAN, and dimension anomalies;
- footer, guide, header, or page number materialized as data;
- spelling aliases that may conceal distinct meanings;
- revisions, retakes, alternate captures, and page generations;
- unresolved relation scope and chains of ditto marks;
- free-text fields used as fallback storage for unmodeled columns.

Read [audit-contract.md](references/audit-contract.md) for states, evidence fields, prioritization, and stop conditions.

## Human-decision packet

Each item must include:

- stable issue and format IDs;
- page ID, source hash, image path, and image dimensions;
- block/row/column locator and bbox availability;
- raw value and OCR state;
- visible headers and neighboring rows;
- same-format and same-identifier comparisons;
- related sidecar entries;
- observation;
- strongest and alternative hypotheses;
- confidence, evidence, and falsification condition;
- data-model and normalization impact;
- one human question;
- recommended action.

Deduplicate items into decision clusters. A single format-level decision may resolve many page-level warnings, but retain reverse links to every original issue.

## Priority

Order work by dependency:

1. provenance and artifact completeness;
2. physical format and column mapping;
3. record grain and identifiers;
4. value semantics and units;
5. relations and revisions;
6. normalization;
7. isolated character uncertainty.

Resolve high-impact format decisions before asking repetitive cell questions.

## Completion

Report counts by format, category, state, and decision status. Confirm:

- every included page has all required artifacts and validator evidence;
- every human question links to an original image;
- raw observations remain unchanged;
- proposals and human corrections are separate;
- unresolved items are preserved without forced inference.
