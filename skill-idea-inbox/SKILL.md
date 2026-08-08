---
name: skill-idea-inbox
description: Capture, review, and update ideas for future global Codex Skills and cross-project agent capabilities in a persistent global dictionary outside individual projects. Use when the user says an idea should be remembered globally, saved for later, added to a Skill backlog, not lost, or kept in a failure-dictionary-like place, even when they do not explicitly ask to build the idea now.
---

# Skill Idea Inbox

Preserve future Skill and agent-workflow ideas without mixing them into project notes.

## Storage

Use the global event ledger:

```text
~/.codex/skill-idea-inbox/ideas.jsonl
```

Keep mutable ideas outside the Skill folder so Skill updates do not erase them. Store no full prompt, response, tool output, secret, or client data.

## Capture workflow

1. Distill the request into:
   - short title;
   - problem or friction;
   - desired outcome;
   - smallest useful first version;
   - later expansion;
   - related system;
   - priority and next step.
2. Search the current inbox for a matching title or outcome.
3. If a matching idea exists, append an update event instead of creating a duplicate.
4. Otherwise add a new idea with `scripts/idea_inbox.py add`.
5. Return the stable idea ID and a one-sentence summary.
6. Do not start implementation unless the user also asked to build it.

## Statuses

Use:

- `inbox`: captured but not assessed;
- `candidate`: worth designing;
- `planned`: implementation scope is defined;
- `building`: work has started;
- `implemented`: usable capability exists;
- `rejected`: deliberately not pursuing;
- `superseded`: replaced by another idea.

## Review workflow

Use `list` to show the latest state of each idea. Prefer an actionable queue sorted by priority and creation time. Use `show` for the full current record and event history. Use `update` for status, priority, next step, or added detail.

## Commands

```powershell
python -X utf8 scripts/idea_inbox.py add `
  --title "Short title" `
  --problem "Observed friction" `
  --desired-outcome "Outcome" `
  --mvp "Smallest useful version" `
  --later "Later expansion" `
  --related-system "Mira" `
  --priority high `
  --next-step "Concrete next action" `
  --tags artifact-review local-ui

python -X utf8 scripts/idea_inbox.py list
python -X utf8 scripts/idea_inbox.py show IDEA_ID
python -X utf8 scripts/idea_inbox.py update IDEA_ID --status planned --next-step "..."
```

The script prints JSON. Treat the ledger as the source of truth; do not rely on conversation memory alone.

## Boundaries

- Do not store project tasks that belong in that project's task ledger.
- Do not treat an idea as an approved implementation.
- Do not invent deadlines, budgets, or owners.
- Do not put sensitive business content in the global ledger.
- Ask only when the idea cannot be captured safely without consequential interpretation.
