---
name: progress-verifier
description: Verify task and milestone completion from observable evidence, detect partial or stale outcomes, record verification, and unlock dependent work. Use when an agent or human reports completion, before marking a task done, during periodic progress checks, or when deciding whether downstream work may start.
---

# Progress Verifier

1. Read the task acceptance criteria and expected output.
2. Inspect the actual artifact, test, external state, or human-provided evidence.
3. Distinguish completed, partially completed, blocked, failed, and unverifiable.
4. Run proportionate checks; do not accept a plan, claim, or generated file as proof by itself.
5. Record evidence and timestamp.
6. Mark `done` only when every required acceptance condition is met.
7. Unlock dependent tasks only after successful verification.
8. On failure, preserve evidence and return the smallest concrete recovery action.

Use `transition --status done --evidence ...` only after verification.
