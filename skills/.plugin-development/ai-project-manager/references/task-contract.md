# Task contract

Every task uses these fields:

| Field | Meaning |
|---|---|
| `id` | Stable identifier |
| `projectId` | Owning project |
| `title` | Concrete action or decision |
| `objective` | Outcome advanced by the task |
| `assigneeType` | `agent`, `human`, or `service` |
| `assignee` | Named actor or capability |
| `requiredCapability` | Why this actor is appropriate |
| `status` | `backlog`, `ready`, `in-progress`, `waiting-human`, `waiting-service`, `blocked`, `verification`, `done`, or `cancelled` |
| `priority` | `critical`, `high`, `normal`, or `low` |
| `due` | ISO date/time or empty |
| `estimatedMinutes` | Human effort estimate when relevant |
| `dependencies` | Task IDs that must be verified first |
| `expectedOutput` | Observable deliverable |
| `verification` | Completion test |
| `reason` | Routing or request rationale |
| `evidence` | Timestamped completion or state evidence |
| `lastReminderAt` | Duplicate-reminder guard |

Do not store raw secrets, authentication material, or unnecessary conversation transcripts.
