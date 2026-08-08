---
name: human-task-requester
description: Turn human-only or human-authorized work into a concise, executable request with context, prepared materials, deadline, estimated effort, completion signal, and delay impact. Use when Codex must ask a person to decide, approve, contact someone, negotiate, pay, inspect physically, operate an inaccessible system, or provide subjective acceptance.
---

# Human Task Requester

Make the human contribution small and unambiguous.

1. Explain why a human is required.
2. Ask for one concrete action or decision.
3. Prepare drafts, links, options, checklists, or scripts first.
4. State due date only when evidenced or explicitly chosen.
5. Include estimated minutes, expected output, completion method, blocked dependents, and impact of delay.
6. Offer a default recommendation for decisions when evidence supports one.
7. Record the request as `assigneeType=human` and `status=waiting-human`.
8. Avoid duplicate reminders and avoid bundling unrelated decisions.

Format:

```text
Request:
Reason:
Prepared:
Estimated effort:
Due:
Completion signal:
Impact of delay:
```
