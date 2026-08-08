# OCR semantic audit contract

## State boundaries

- `read` / exact: directly visible transcription.
- `blank`: physically blank slot, not merely missing extraction.
- `illegible`: characters exist but cannot be read.
- `ambiguous`: two or more plausible readings or scopes exist.
- Relation uncertainty belongs in a relation sidecar and review reason; do not erase the marks.

## Page-wide exception boundary

Permit a page-wide exception only when artifact construction itself is unsafe or impossible:

- image missing, corrupt, or unreadable;
- manifest provenance mismatch;
- required page identity missing;
- image effectively absent or cut so physical blocks cannot be observed;
- runner/helper failure prevents safe storage or validation.

Density, handwriting, strike-through, overwrite, spread layout, ditto marks, or relation lines are not page-wide exception reasons.

## Evidence fields

Separate:

1. observed image fact;
2. OCR raw and state;
3. strongest hypothesis;
4. alternatives;
5. confidence;
6. supporting evidence;
7. falsification condition;
8. structural impact;
9. recommended action;
10. human decision.

## Recommended actions

- `keep_raw`
- `remap_column`
- `split_cell`
- `link_relation`
- `preserve_history`
- `exclude_row`
- `reclassify_state`
- `needs_domain_decision`

## Human-review UX

- Ask one result-changing question at a time.
- Show the actual original image, not only a path or OCR excerpt.
- Indicate the exact region to inspect.
- Offer a provisional hypothesis before asking.
- Prefer a format-level question when it safely resolves repeated instances.
- Never write a provisional decision into the OCR original.

## Review package integrity

Include only pages whose stored result, warning assessment, relation sidecar, and validators pass. Verify:

- workbook, image, and OCR source JSON are present;
- hashes and links match;
- ambiguous, illegible, and blank styling is distinguishable;
- formulas, Excel Tables, conditional formatting, and calculation chains are absent when static review is required;
- OCR source JSON hashes are unchanged.

## Stop conditions

Stop automatic interpretation and preserve unknown when:

- format membership remains unresolved;
- a relation has multiple plausible targets;
- business meaning cannot be inferred from image evidence and comparisons;
- normalization would discard raw distinctions;
- a change would require modifying source OCR without human approval.
