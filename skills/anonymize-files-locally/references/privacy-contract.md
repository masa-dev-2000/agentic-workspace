# Privacy and review contract

## Trust boundary

Codex may know the requested input path, output path, counts by category, hashes, processing
status, and sanitized error codes. Codex must not receive the source, anonymized text, matched
values, replacement map, diff, or local-LLM request and response bodies.

The local process reads the source and communicates only with a loopback Ollama-compatible HTTP
endpoint. The human review window is outside Codex automation and reads the source and draft
directly from disk.

Folder mode stores relative paths only in a local batch manifest. Codex receives aggregate counts
and status, not the file list. The output tree is a separate sibling directory by default.

## States

- `pending_review`: local processing and deterministic residual checks completed; no human
  decision exists.
- `approved`: the human approved the exact source and draft hashes in the local review window.
- `rejected`: the human rejected that exact draft.
- `stale`: the source or draft hash changed after processing or review.
- `failed`: processing stopped with a sanitized error code.

Only `approved` is completion evidence. Approval does not prove perfect anonymization; it proves
that the human reviewed the recorded draft.

## Residual checks

The deterministic scanner counts patterns for email addresses, phone-like numbers, Japanese
My Number candidates, payment-card-like values passing Luhn, URLs with credentials, and IPv4
addresses. It returns only category counts. A non-zero count does not expose the value and keeps
the item reviewable; the human decides whether it is sensitive or intentionally retained.

CSV files are parsed and rebuilt by deterministic local code. The local LLM receives unique cell
values as a JSON array and must return the same number of sensitivity decisions. Cells classified
as non-sensitive are copied from the source by deterministic code, ignoring any model rewrite.
When a header row is present, only columns with identifier-related headers, plus cells matching
deterministic identifier patterns, are sent to the model. Generic note, amount, category, and date
columns therefore remain byte-for-value unchanged.
Row and column counts never depend on model-generated CSV syntax. Files with more than 2,000
unique non-empty cells stop with a sanitized error instead of sending an unbounded request.

## Files

The source is never overwritten. A sibling draft and a metadata-only manifest are created. The
manifest contains paths, hashes, model identifier, timestamps, category counts, and status, but
no source fragments or replacement values.

For a folder, each supported regular file receives its own metadata manifest under the output
folder's `.review` directory. A batch manifest links those manifests and records only paths,
sanitized error codes, aggregate state, model, limits, and timestamps. Symbolic links are skipped.
