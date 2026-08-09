# Planning handoff evaluation cases

1. Plan digest remains unchanged after GAN evidence and execution enrichment; `enrichment_digest` is separate.
2. Missing GAN review evidence, No-Go, degraded mandatory coverage, or unknown completion guarantee blocks adaptive handoff.
3. Planning→Adaptive→GAN→execution preserves the same plan digest and task write scopes.
4. A changed plan is rejected against the old digest and invalidates prior approval/dispatch.
5. Agent-team execution is allowed only for `team` mode with a valid team lease; PM ledger ownership remains with project-orchestrator.
