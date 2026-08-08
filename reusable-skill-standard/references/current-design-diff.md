# Current design diff

## Already present

- `SKILL.md` is the executable human-readable procedure and trigger description.
- Adaptive Orchestrator provides staged execution, Agent Registry routing, retry, verification, approval separation, and body-free telemetry.
- Stage Runner is the state authority for PLAN, REVIEW, IMPLEMENT, VERIFY, and REPORT.
- Agent Policy is separate from Agent Registry and prevents an Agent from granting authority.
- Provider usage is recorded only when authoritative usage is returned; unavailable values are not estimated.
- Existing Skills are Git-managed local folders with optional scripts, references, and assets.

## Missing or inconsistent

- No common machine-readable `skill.yaml` contract across all Skills.
- Tool authority is not uniformly expressed as read, suggest, draft, write, and destructive.
- Gatekeeper is not yet a universal adapter boundary for GitHub, Supabase, Google, email, or internal databases.
- Sandbox execution is not a required portable interface for generated code or commands.
- Model routing exists for orchestrated Sub Agents but is not a universal Skill metadata contract.
- Generated dashboards and mini-apps are not formally separated from Skill instructions.
- Skill-specific input/output schemas and prompt-injection test cases are not universal.
- Audit contracts must distinguish proposed, approved, and executed operations consistently.

## Migration rule

Do not rewrite all existing Skills at once. Add `skill.yaml`, permission policy, schemas, and tests when a Skill is next changed. Keep legacy `SKILL.md` behavior until the new contract validates, then register the contract version.
