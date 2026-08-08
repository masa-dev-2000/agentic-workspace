# Gadget and generated artifact contract

Generated dashboards, mini-apps, reports, and code are output artifacts, not Skill instructions.

The Skill declares:

- `view_model`: fields and evidence references that may be displayed.
- `allowed_actions`: operation IDs, each mapped to a permission level.
- `data_scope`: project, resource, and privacy boundaries.
- `refresh_policy`: when the view may retrieve new data.
- `artifact_ref`: immutable generated UI or code artifact reference.
- `verification`: schema, visual, functional, and security checks required before delivery.

The UI adapter may be replaced without changing the Skill. A generated UI must not receive credentials, invent authority, or call an external service outside Gatekeeper.
