# App contract

Store `app-contract.json` in the project root:

```json
{
  "schemaVersion": 1,
  "name": "short product name",
  "outcome": "observable user outcome",
  "personas": [{"name": "primary user", "need": "job to be done"}],
  "primaryJourney": ["entry", "core action", "verified result"],
  "acceptanceCriteria": ["observable criterion"],
  "nonGoals": ["explicitly excluded scope"],
  "stack": {"runtime": "web", "frontend": "react-typescript-vite", "backend": "none"},
  "consequentialDecisions": [],
  "browserViewports": [{"width": 1440, "height": 900}, {"width": 390, "height": 844}]
}
```

Infer reversible presentation details. Ask about identity, public visibility, sensitive data, payments, legal claims, destructive operations, paid services, or material workflow changes.

