# Provider-neutral interfaces

Each interface is an injected boundary. A Skill declares the capability it needs; the runtime selects the implementation for the current environment.

```text
AuthProvider
  resolve_actor() -> actor_ref
  authorize(actor_ref, skill_ref, resource_ref, operation, scope) -> decision

ToolProvider
  invoke(tool_ref, bounded_args, capability_token) -> result_ref

Gatekeeper
  check(actor, skill, project, resource, operation, limit, expiry) -> allow | approval | deny
  issue_scoped_token(decision) -> opaque_token

ModelProvider
  start(model_key, bounded_request) -> run_ref
  wait(run_ref) -> result_ref
  usage(run_ref) -> observed_usage | unavailable

ExecutionSandbox
  run(artifact_ref, limits, network_policy, secret_policy) -> execution_ref
  inspect(execution_ref) -> result_ref

StateStore / FileStore
  read(ref) -> value_ref
  append(event) -> receipt_ref
  mutate(ref, expected_revision, change_set) -> receipt_ref

AuditLogger
  record(metadata_only_event) -> evidence_ref

ApprovalProvider
  request(scope, digest, expiry) -> approval_requested
  verify(approval_ref, scope, digest) -> approved | invalid

Scheduler / NotificationProvider / KnowledgeProvider
  schedule(job) -> schedule_ref
  notify(target, bounded_message_ref) -> receipt_ref
  retrieve(query, scope) -> evidence_refs
```

Implementations may be Cloudflare Access, Keycloak, Authentik, Supabase Auth, LiteLLM, Docker, Firecracker, gVisor, Kubernetes Job, PostgreSQL, Redis, MinIO, S3, Temporal, n8n, or local equivalents. The Skill must not import any implementation directly.

## Required invariants

- Gatekeeper checks happen immediately before external side effects.
- Capability tokens are scoped, expiring, and never placed in model context.
- Sandbox network is deny-by-default and writes are bounded.
- Audit records contain metadata and opaque evidence references, not raw bodies or secrets.
