# Vendor Adapters

The workspace core is provider-neutral: `skills/` follows the open Agent Skills
specification (agentskills.io) — portable frontmatter only (`name`, `description`,
optionally `license`, `compatibility`, `metadata`, `allowed-tools`), enforced by
`scripts/validate_workspace.py`. Everything vendor-specific is an adapter: a thin
wiring layer that connects one agent service to the neutral core.

## Wiring matrix (current)

| Service | Skills | Agents | Instructions | Hooks |
|---|---|---|---|---|
| Claude Code | `~/.claude/skills` → symlink → `~/.codex/skills` → junction → `skills/` | `~/.claude/agents` → junction → `agents/claude/` | `~/.claude/CLAUDE.md` (copy: `config/CLAUDE.global.md`) | `~/.claude/settings.json` + scripts (copy: `hooks/claude/`) |
| OpenAI Codex CLI | `~/.codex/skills` (junction) and `~/.agents/skills` (junction) | — (no cross-vendor agent format) | `AGENTS.md` in `skills/` | `~/.codex/hooks.json` (copy: `hooks/codex/`) |
| Gemini CLI | `~/.agents/skills` (junction) — native path | — | AGENTS.md (native) | — |
| Any new service | point it at `~/.agents/skills` or junction its skill dir to `skills/` | add `agents/<vendor>/` if it has an agent format | derive from `config/` | add `hooks/<vendor>/` if supported |

`~/.agents/skills/` is the emerging cross-tool location (read natively by Codex CLI
and Gemini CLI). All paths above resolve to the single `skills/` tree in this repo.

## Onboarding checklist for a new agent service

1. Skills: junction/symlink the service's skill directory to this repo's `skills/`
   (or configure the service to read `~/.agents/skills`). The portable frontmatter
   subset is guaranteed by the validator, so skills load without changes.
2. Agents: if the service has its own agent/subagent format, create
   `agents/<vendor>/` and keep definitions THIN — routing description, tool
   restrictions, and a pointer to the owning skill. Procedure logic belongs in
   skills (portable), not agent files (vendor-specific, no cross-vendor standard).
3. Instructions: generate the service's instruction file (AGENTS.md dialect or
   equivalent) from `config/`; do not fork the content.
4. Hooks/automation: add `hooks/<vendor>/` and add a `kind: copy` entry to
   `config/wiring.json` so copies cannot drift silently
   (criterion: drift-coverage-completeness; schema: `config/wiring.schema.md`).
5. Run `python -X utf8 scripts/validate_workspace.py` and update the wiring
   matrix above.

## Rules

- Never add vendor-specific frontmatter to `skills/*/SKILL.md`. Vendor behavior
  goes in the adapter layer (agent files, service config, hooks) or the
  spec's `metadata` map.
- One canonical content tree; adapters may only link, generate, or copy-with-drift-check.

## Distribution

When distributing this workspace (or a subset) to other machines, people, or teams, package it in the Agent Plugins 1.0 format (agent-plugins.org): `plugin.json` manifest + `skills/` (already spec-compliant) + `mcp.json`. Do not adopt it for local wiring — junctions already serve that purpose.
