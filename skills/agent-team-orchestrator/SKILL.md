---
name: agent-team-orchestrator
description: Coordinate bounded subagents in projects that use a persistent PM ledger. Use when Codex must parallelize independent project work, track who owns each task with execution leases and heartbeats, verify completion, keep safe implementation slots filled, or route an idle agent to the next safe task without waiting for another user prompt.
---

# Agent Team Orchestrator

Keep the root agent as the sole orchestrator and the persistent PM ledger as task state authority.

## Coordinate

1. Read applicable project instructions, current state, dependencies, and ledger evidence.
2. Split work only into independent, bounded tasks with:
   - stable task ID;
   - objective and acceptance evidence;
   - required capability;
   - dependencies and priority;
   - explicit write boundary.
3. Assign one task per agent. Do not duplicate an active or stale execution lease.
4. After the agent accepts the follow-up, record the execution lease through the project's
   authoritative writer. Heartbeat long work and close or clear the lease when work stops.
5. Track agent, task, lease state, heartbeat, artifacts, tests, and blockers in the ledger.

## Refill Idle Agents

Treat an agent completion notification as a refill event. After an agent finishes:

1. Inspect its artifacts rather than trusting the completion claim.
2. Use the project's progress verifier before recording completion or unlocking dependents.
3. Recompute eligible work from the ledger.
4. Select the highest-priority independent task whose dependencies are verified, write boundary
   does not overlap active work, and capability matches.
5. If that task is safe, reversible, already authorized, and non-Production, let the root agent
   issue `followup_task` immediately; do not wait for another user prompt.
6. Record the new execution lease only after the agent accepts the follow-up.
7. If verification fails, assign a bounded repair task without unlocking dependents.
8. Repeat while agent slots and eligible tasks remain.
9. Do not let a subagent self-assign or spawn successors.
10. If no safe task exists, leave the agent idle and state the blocking dependency or approval.

Root-controlled immediate refill is not automatic ledger reassignment. The root remains
responsible for artifact verification, task selection, `followup_task`, and ledger updates.

## Preserve Integration Ownership

- Give subagents only their bounded implementation and test files.
- Do not let subagents update shared project status, roadmap, handoff, release, or PM source-of-truth
  documents. The root agent verifies and integrates those changes.
- Resolve overlapping writes and cross-task conflicts at the root.

## Fail Closed

- If all ledger writers do not share a proven transactional CAS or lock, keep automatic ledger
  apply, automatic reassignment, stale-lease recovery, and lease stealing disabled. Return a
  proposal for the root instead.
- Never automatically select Production, destructive, deletion, financial, legal, customer-facing,
  public, or human-approval work.
- Never infer completion from status text or an agent report. Require verifier-issued evidence
  linked to the task before dependents become eligible.
- Do not expose secrets, raw ledger contents, local paths, model identifiers, or unnecessary actor
  identifiers in agent-facing status.
