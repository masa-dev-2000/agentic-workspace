# Repository instructions

This repository is the provider-neutral source of truth for the personal agentic
workspace. Keep reusable behavior in `skills/`; keep vendor-specific wiring thin and
inside the applicable adapter, agent, hook, or configuration layer.

## Working agreement

- Make the smallest change that solves the requested problem. Do not refactor adjacent
  code or duplicate canonical content for convenience.
- Project-specific/customer data, ledgers, credentials, generated logs, and other live
  state do not belong in this shared repository. Keep them with the project that owns
  them or in an external location declared by `config/wiring.json`.
- Before pushing a non-trivial change, run:
  - `python -X utf8 scripts/validate_workspace.py --no-live`
  - `python -X utf8 -m unittest discover -s scripts/tests -v`
- Use a separate, read-only Codex review pass before merge. A passing Codex review does
  not replace CI or the owner's final decision. Do not auto-merge.

## Code Review Rules

### Canonical content and wiring

- Flag changes that duplicate reusable Skill/configuration content in a vendor-specific
  location, or add a live attachment point without declaring and drift-checking it in
  `config/wiring.json`. Safe path: keep one canonical source and use a thin adapter,
  link, generated file, or copy with an explicit drift check.

### Data locality and credential boundaries

- Flag changes that place project/customer-specific records, operational ledgers,
  secrets, tokens, private logs, or another repository's issue data in this shared
  workspace. Safe path: store the data in the owning project or a declared external
  state location; keep only reusable cross-project behavior here.

### Enforcement integrity

- Flag changes that claim a safety, validation, or governance rule without an executable
  enforcement path, or that weaken/skip a validator, hook, or CI gate silently. Safe
  path: add a reproducible failing case and passing verification; when a check must be
  reduced, name exactly what is skipped, why, and where equivalent coverage remains.
