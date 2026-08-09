# agentic-workspace

Single source of truth for the personal agentic harness shared by Codex and Claude Code.

- `skills/` — skill collection (live: junctioned from `~/.codex/skills`; `~/.claude/skills` symlinks there)
- `agents/claude/` — Claude Code subagent definitions
- `hooks/claude/` — Claude Code hook scripts (referenced from `~/.claude/settings.json`)
- `hooks/codex/` — Codex hooks.json + hook scripts
- `commands/claude/` — Claude Code slash commands
- `config/` — global CLAUDE.md snapshots

Live locations are junctions/symlinks into this repo (skills) or synced copies (see each dir).

Validation: `python -X utf8 scripts/validate_workspace.py` — agent frontmatter, copy-dir drift vs live locations, criteria schema/index (`--fix` regenerates the index). Run before every push.
