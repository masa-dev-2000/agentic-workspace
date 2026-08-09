# agentic-workspace

Provider-neutral single source of truth for a personal agentic harness — usable from
ANY agent service. The core (`skills/`) follows the open Agent Skills specification
(agentskills.io); everything vendor-specific lives in thin adapter layers
(see `adapters/README.md` for the wiring matrix and onboarding checklist).
Currently wired: Claude Code, OpenAI Codex CLI, and the neutral `~/.agents/skills`
path read natively by Codex CLI and Gemini CLI.

- `skills/` — skill collection (live: junctioned from `~/.codex/skills`; `~/.claude/skills` symlinks there)
- `agents/claude/` — Claude Code subagent definitions (LIVE via junction from `~/.claude/agents` — a file here is immediately active)
- `agents/proposed/` — staged agent proposals drafted by agent-steward; go live only after explicit human approval moves them to `agents/claude/`
- `hooks/claude/` — Claude Code hook scripts (referenced from `~/.claude/settings.json`)
- `hooks/codex/` — Codex hooks.json + hook scripts
- `commands/claude/` — Claude Code slash commands
- `config/` — global CLAUDE.md snapshots

Live locations are junctions/symlinks into this repo (skills) or synced copies (see each dir).

Validation: `python -X utf8 scripts/validate_workspace.py` — agent frontmatter, wiring registry (`config/wiring.json`, schema: `config/wiring.schema.md`) and copy-dir drift derived from it, criteria schema/index (`--fix` regenerates the index), leaked-ledger guard. Run before every push.

Wiring: `config/wiring.json` declares every junction/symlink/copy/ledger/scheduled-task attachment point between this repo and the live machine. `python -X utf8 scripts/bootstrap_workspace.py --check` verifies it read-only; `--apply` reproduces it; `--markdown` emits a table.
