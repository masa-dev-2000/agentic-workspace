---
name: task-router
description: Decide whether ready project work belongs to an AI agent, a human, or an external service by evaluating capability, authority, reversibility, risk, cost, deadline, and verification. Use when assigning tasks, delegating work, deciding what Codex may do automatically, or separating human-only work from automatable work.
---

# Task Router

Route by required capability and authority, not convenience.

1. Confirm the task has a concrete expected output and verification rule.
2. Choose `agent` when the work is digital, authorized, safely reversible, and objectively verifiable.
3. Choose `human` when it requires identity, physical presence, relationship context, payment authority, negotiation, private judgment, or explicit approval.
4. Choose `service` when a connected system can perform a bounded deterministic action.
5. Split mixed tasks so AI prepares inputs and the human performs only the irreducible step.
6. Reject routing when the required authority or acceptance criteria are unknown.
7. Record `assigneeType`, `assignee`, `requiredCapability`, `status`, and dependencies in the ledger.

Never treat a human as a generic approval button. Minimize their effort by preparing context, drafts, choices, and completion signals.
