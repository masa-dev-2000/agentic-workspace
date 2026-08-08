# Standard Skill execution flow

```text
user request
  -> candidate Skill resolution
  -> trigger and exclusion check
  -> context references and data classification
  -> Gatekeeper identity, resource, scope, and authority check
  -> execution plan and model/Agent selection
  -> independent review when required
  -> tool call through provider adapter
  -> output schema and acceptance verification
  -> human approval if the next operation is write/destructive
  -> Gatekeeper-mediated execution
  -> postcondition and rollback verification
  -> body-free audit record
```

The model may propose routing, tools, and operations. The Gatekeeper and fixed policy decide whether they are allowed. A failed or unknown external operation must not be retried blindly; reconcile state first.
