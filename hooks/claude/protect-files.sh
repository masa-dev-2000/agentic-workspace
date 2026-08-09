#!/bin/bash
# PreToolUse hook: Edit/Writeツール使用時に重要ファイルへの変更をブロック
# 対象: .git/, .claude/settings.json, SSH鍵, クレデンシャルファイル等

INPUT=$(cat /dev/stdin)

# ファイルパスを取得
if command -v jq &>/dev/null; then
  FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.path // ""')
elif command -v python3 &>/dev/null; then
  FILE_PATH=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); ti=d.get('tool_input',{}); print(ti.get('file_path','') or ti.get('path',''))")
elif command -v python &>/dev/null; then
  FILE_PATH=$(echo "$INPUT" | python -c "import sys,json; d=json.load(sys.stdin); ti=d.get('tool_input',{}); print(ti.get('file_path','') or ti.get('path',''))")
else
  exit 0
fi

# パスが空なら通過
[ -z "$FILE_PATH" ] && exit 0

# パスを正規化（Windows: バックスラッシュをスラッシュに）
NORM_PATH=$(echo "$FILE_PATH" | sed 's|\\|/|g' | tr '[:upper:]' '[:lower:]')

# === .git 内部ファイルの保護 ===
if echo "$NORM_PATH" | grep -qiE '(/|^)\.git/'; then
  echo "BLOCKED: .git/内部のファイルを直接変更することはできません" >&2
  exit 2
fi

# === Claude設定ファイルの保護 ===
if echo "$NORM_PATH" | grep -qiE '\.claude/settings\.json$|\.claude/settings\.local\.json$'; then
  echo "BLOCKED: Claude設定ファイルの直接変更は禁止されています（手動で変更してください）" >&2
  exit 2
fi

# === SSH鍵・クレデンシャルの保護 ===
if echo "$NORM_PATH" | grep -qiE '\.ssh/|\.gnupg/|\.aws/credentials|\.azure/'; then
  echo "BLOCKED: 認証情報ファイルの変更は禁止されています" >&2
  exit 2
fi

# === .env ファイルの保護 ===
if echo "$NORM_PATH" | grep -qiE '(^|/)\.env(\.|$)'; then
  echo "BLOCKED: .envファイルの変更は禁止されています（手動で変更してください）" >&2
  exit 2
fi

# === Windows系システムファイルの保護 ===
if echo "$NORM_PATH" | grep -qiE '^c:/windows/|^c:/program files'; then
  echo "BLOCKED: Windowsシステムディレクトリへの書き込みは禁止されています" >&2
  exit 2
fi

exit 0
