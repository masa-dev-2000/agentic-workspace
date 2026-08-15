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

## External PR review adapter: CodeRabbit

CodeRabbit is an optional post-PR reviewer, not a replacement for the provider-neutral
`review-agent` Skill or Claude's read-only `adversarial-reviewer`.

The root `.coderabbit.yaml` is version-controlled review policy. It is inert until the
CodeRabbit GitHub App is installed and granted access to this repository. Installing the App is an
account-level GitHub action and cannot be accomplished by committing repository files.

After installation, the repository configuration:

- reviews non-draft PRs targeting the default branch;
- incrementally reviews later pushes;
- uses low-noise, defect-first review settings;
- applies repository-specific instructions for Skills, Hooks, Runtime scripts, agents, commands,
  configuration, tests, and documentation;
- never enables auto-merge or treats a bot review as owner approval.

Manual PR commands remain available through GitHub comments:

```text
@coderabbitai review
@coderabbitai full review
```

The CodeRabbit CLI and its official portable Skills are optional local adapters. Do not vendor a
second canonical review Skill into `skills/`: this workspace already owns the provider-neutral
review contract. A local CodeRabbit integration may call its CLI as an additional reviewer, but it
must preserve the boundaries in `docs/CODE_REVIEW.md`:

- an implementation agent validates every external finding before changing code;
- CodeRabbit autofix is not an authority and is not applied blindly;
- CodeRabbit failure or absence is recorded, not hidden behind a manual review presented as if it
  came from CodeRabbit.

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

## The OS is another adapter axis

The vendor axis above (Claude Code / Codex CLI / Gemini CLI) is not the only
place this repo adapts to its environment: the operational scripts
(`bootstrap_workspace.py`, `register_tasks.py`, `health_check.py`,
`backup_ledgers.py`) also run on Windows, macOS, or Linux, and every
difference between those three (link mechanism, scheduler, shell, default
paths) is isolated the same way vendor differences are — one thin adapter
module, `scripts/platform_adapter.py`, that the portable script logic calls
into rather than branching on `sys.platform` itself. See
`config/wiring.schema.md` ("kind semantics per platform") and
`docs/OPERATIONS.md` ("Other platforms") for what it covers.

## Distribution

When distributing this workspace (or a subset) to other machines, people, or teams, package it in the Agent Plugins 1.0 format (agent-plugins.org): `plugin.json` manifest + `skills/` (already spec-compliant) + `mcp.json`. Do not adopt it for local wiring — junctions already serve that purpose.
