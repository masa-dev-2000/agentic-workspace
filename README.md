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

Validation: `python -X utf8 scripts/validate_workspace.py` — agent frontmatter, wiring registry (`config/wiring.json`, schema: `config/wiring.schema.md`) and copy-dir drift derived from it, criteria schema/index (`--fix` regenerates the index), Skill composition against `skills/AGENTS.md` (SKILL.md body length, machine-specific absolute paths, inline schema blocks, command-block density), leaked-ledger guard. Run before every push.

Composition violations that predate that check are acknowledged one skill and one rule at a time in `config/skill-composition-acknowledged.json`; each entry names the issue that tracks fixing it, and an entry whose skill no longer violates the rule fails the validator, so the list can only shrink. There is no blanket-mute form. An unacknowledged warning also exits non-zero — every consumer of this validator gates on the exit code alone, so a warning that stayed green would be a warning nobody ever resolves (criterion `validator-signal-hygiene`). Unit tests: `python -X utf8 -m unittest discover -s scripts/tests -v`.

This is mechanized by `.githooks/pre-push`, which runs the same validator and
blocks the push on failure. It is not enabled by default — enable it once per
clone/worktree with:

```
git config core.hooksPath .githooks
```

`--no-live` skips the live-filesystem checks (drift + wiring liveness), which
require this machine's home directory (`~/.claude`, `~/.codex`, etc.) and
therefore cannot run on a CI runner; `.github/workflows/validate.yml` runs
`scripts/validate_workspace.py --no-live` on every pull request and push. The
pre-push hook (which does run on this machine) runs the full validator with
live checks from the main checkout; from a linked `git worktree` it downgrades
to `--no-drift`, which skips only the drift and wiring-liveness comparisons
(the junctions resolve to the main checkout, so from a worktree they compare
unrelated trees) and keeps every other check, including RULEBOOK enforcement.
The hook prints on stderr which groups it skipped and why, so a reduced gate is
never silent.

Review: `docs/CODE_REVIEW.md` is the canonical layered pipeline. Implementation verification is
followed by a fresh read-only agent review, deterministic CI, optional CodeRabbit PR review through
`.coderabbit.yaml`, and finally the human owner's decision. CodeRabbit is supplementary and remains
inactive until its GitHub App is installed; it never replaces the local `review-agent` /
`adversarial-reviewer` step or human approval.

Change management is deliberately minimal: this is a single-owner repo, so
GitHub branch protection is not enabled (the owner either bypasses it or is
taxed by it — either way it adds no real control). The pre-push hook plus CI
validator are the enforcement mechanism instead.

Wiring: `config/wiring.json` declares every junction/symlink/copy/ledger/scheduled-task attachment point between this repo and the live machine. `python -X utf8 scripts/bootstrap_workspace.py --check` verifies it read-only; `--apply` reproduces it; `--markdown` emits a table.

Operations: see `docs/OPERATIONS.md` for blast radius, cadence, and recovery runbooks.
