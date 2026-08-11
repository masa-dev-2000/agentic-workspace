#!/usr/bin/env python3
"""Helper invoked by guard-deletion.sh: inspects a PreToolUse payload for
destructive operations and prints an "ask" permission decision (never
"deny" - the point of this hook is a human confirmation gate, not a block).

Prints NOTHING and exits 0 when nothing destructive/noteworthy is detected
(silent pass-through, matching validate-command.sh / protect-files.sh's
convention of only speaking up when there is something to say).

Write/Edit are explicitly OUT OF SCOPE: they only overwrite file content,
they do not delete anything, so protect-files.sh already covers the files
that matter there.
"""
import json
import re
import sys

# Any regex match against the raw command text is enough here; this is a
# confirmation gate, not a security boundary, so simple substring/regex
# checks (mirroring validate-command.sh's own style) are an intentional,
# proportionate choice over a full shell parser.

# --- SAFE_SCRATCH_PATTERNS ---------------------------------------------
# Deletions under these paths pass SILENTLY (no ask) - they are obvious,
# low-stakes scratch/cache/build areas and asking about them every time
# would just teach the user to reflexively click through the prompt,
# defeating the gate for the deletions that actually matter.
#   - %TEMP%/$TMPDIR/'/tmp/': OS-level scratch space, nothing of record lives here
#   - the Claude session scratchpad dir (AppData\Local\Temp\claude\...\scratchpad):
#     documented as a throwaway working area for this harness
#   - node_modules, __pycache__, .pytest_cache: regenerable dependency/cache dirs
#   - dist, build, .next, target: regenerable build output dirs
# Kept tight and enumerated on purpose - broadening it silently would widen
# a noise-avoidance allowlist into a way to bypass the gate.
SAFE_SCRATCH_PATTERNS = re.compile(
    r"(\$TEMP\b|\$TMPDIR\b|%TEMP%|/tmp/"
    r"|[\\/][Tt]emp[\\/]claude[\\/][^\\/]*[\\/]scratchpad"
    r"|[\\/]node_modules([\\/]|$)"
    r"|[\\/]__pycache__([\\/]|$)"
    r"|[\\/]\.pytest_cache([\\/]|$)"
    r"|[\\/]dist([\\/]|$)"
    r"|[\\/]build([\\/]|$)"
    r"|[\\/]\.next([\\/]|$)"
    r"|[\\/]target([\\/]|$))",
    re.IGNORECASE,
)

PATH_DELETE_CMD_RE = re.compile(r"\brm\b|\brmdir\b|\bdel\b|Remove-Item|\brd\b", re.IGNORECASE)

RM_RE = re.compile(r"\brm\s+(-[A-Za-z]*[rRf][A-Za-z]*\b|--recursive\b|--force\b)", re.IGNORECASE)
RMDIR_RE = re.compile(r"\brmdir\b", re.IGNORECASE)
DEL_RE = re.compile(r"\bdel\b[^\n]*(/s\b|/f\b)", re.IGNORECASE)
REMOVE_ITEM_RE = re.compile(r"Remove-Item[^\n]*(-Recurse\b|-Force\b)", re.IGNORECASE)
RD_RE = re.compile(r"\brd\b[^\n]*/s\b", re.IGNORECASE)
GIT_BRANCH_D_RE = re.compile(r"git\s+branch\s+.*-D\b", re.IGNORECASE)
GIT_PUSH_DELETE_RE = re.compile(r"git\s+push\s+[^\n]*(--delete\b|:[A-Za-z0-9_./-]+)", re.IGNORECASE)
GH_DELETE_RE = re.compile(r"gh\s+(repo|release)\s+delete\b", re.IGNORECASE)

MCP_DELETE_RE = re.compile(r"^mcp__.*(delete|drop|remove)", re.IGNORECASE)

REASON_SUFFIX = "RULEBOOK R2に従い依存関係台帳を確認してから実行してください。"


def extract_target(command: str) -> str:
    """Best-effort last path-like token in the command (not a full arg parser)."""
    tokens = [t for t in command.split() if t and not t.startswith("-")]
    skip = {"rm", "rmdir", "del", "remove-item", "rd", "git", "gh", "push", "branch",
            "repo", "release", "delete"}
    candidates = [t for t in tokens if t.lower() not in skip]
    return candidates[-1] if candidates else command.strip()


def emit_ask(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return

    tool_name = data.get("tool_name") or ""
    tool_input = data.get("tool_input") or {}

    # MCP tool names matching delete|drop|remove (case-insensitive).
    if MCP_DELETE_RE.match(tool_name):
        emit_ask(f"MCPツール『{tool_name}』はリソースを削除する可能性があります。{REASON_SUFFIX}")
        return

    # NotebookEdit: edit_mode == "delete" removes a cell.
    if tool_name == "NotebookEdit" and (tool_input.get("edit_mode") or "").lower() == "delete":
        emit_ask(f"Notebookのセル(cell_id={tool_input.get('cell_id', '?')})を削除しようとしています。{REASON_SUFFIX}")
        return

    if tool_name != "Bash":
        return

    command = tool_input.get("command") or ""
    if not command:
        return

    # Noise-avoidance: obvious scratch-path deletions pass silently, checked
    # BEFORE any ask decision, and only for path-deleting commands.
    if PATH_DELETE_CMD_RE.search(command) and SAFE_SCRATCH_PATTERNS.search(command):
        return

    target = extract_target(command)

    if RM_RE.search(command):
        emit_ask(f"『{target}』を再帰的/強制的に削除(rm)しようとしています。{REASON_SUFFIX}")
        return
    if RMDIR_RE.search(command):
        emit_ask(f"ディレクトリ『{target}』を削除(rmdir)しようとしています。{REASON_SUFFIX}")
        return
    if DEL_RE.search(command):
        emit_ask(f"『{target}』を削除(del)しようとしています。{REASON_SUFFIX}")
        return
    if REMOVE_ITEM_RE.search(command):
        emit_ask(f"『{target}』を削除(Remove-Item)しようとしています。{REASON_SUFFIX}")
        return
    if RD_RE.search(command):
        emit_ask(f"ディレクトリ『{target}』を削除(rd /s)しようとしています。{REASON_SUFFIX}")
        return
    if GIT_BRANCH_D_RE.search(command):
        emit_ask(f"ブランチ『{target}』を強制削除(git branch -D)しようとしています。{REASON_SUFFIX}")
        return
    if GIT_PUSH_DELETE_RE.search(command):
        emit_ask(f"リモートブランチ/参照(『{target}』)を削除(git push --delete)しようとしています。{REASON_SUFFIX}")
        return
    if GH_DELETE_RE.search(command):
        emit_ask(f"GitHubリソース『{target}』を削除(gh delete)しようとしています。{REASON_SUFFIX}")
        return


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
