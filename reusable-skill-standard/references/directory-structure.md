# Portable package layout

```text
skill-id/
  SKILL.md
  skill.yaml
  agents/openai.yaml
  schemas/
    input.schema.json
    output.schema.json
  policies/
    permissions.yaml
    approvals.yaml
  prompts/
    system.md
    task.md
  tests/
    cases.yaml
    injection-tests.yaml
  examples/
    input.json
    output.json
  adapters/
    github.md
    supabase.md
  CHANGELOG.md
```

`SKILL.md` and `skill.yaml` are required. All other directories are conditional. Keep provider adapters, generated UI, generated code, and deployment manifests outside the Skill contract when they can be exchanged independently.
