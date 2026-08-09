# Optimizer schema v2 evaluation

- The canonical schema value is `schema_version: candidate_v2`.
- v2 candidates contain target fingerprint, immutable source report digest, concrete change ref, impact, before metrics, and validation plan.
- `tasks >= 20` and a non-zero verified-success baseline are required; otherwise the result is `insufficient-evidence`, never a patch candidate.
- Missing fingerprint, generic scope, duplicate source/target, or validation data creates `insufficient-evidence` rejection.
- Legacy candidates are `legacy-invalid` and cannot be approved or applied.
- Approval requires exact `candidate_id` plus current Registry fingerprint and content digest.
- Application additionally requires an approved status, a local changeset reference, and a matching pre-application content digest.
- Reports are stored as immutable digest-addressed artifacts; `latest.json` is only a convenience pointer.
