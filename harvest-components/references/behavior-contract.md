# Behavior-only contract

A contract may record:

- generic purpose and component kind;
- public inputs, outputs, events, and error states;
- observable interaction and accessibility behavior;
- platform and performance constraints;
- generic edge cases;
- acceptance tests using invented fixtures;
- permitted public standards or documentation.

It must not record:

- source code or pseudocode that mirrors source structure;
- source paths, identifiers, comments, or distinctive strings;
- screenshots, visual assets, branding, or product copy;
- proprietary data models or domain rules;
- implementation-specific decomposition learned from the source.

The Source Analyst signs off that the contract contains behavior only. A fresh Implementation Agent receives only this contract. Preserve both identities and timestamps in provenance.
