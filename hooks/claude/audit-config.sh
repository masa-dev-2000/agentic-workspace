#!/bin/bash
# ConfigChange hook: Claude Code設定変更を監査ログに記録
# 設定がいつ・どこで変更されたかを追跡する

INPUT=$(cat /dev/stdin)
LOG_FILE="$HOME/.claude/audit.log"

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

if command -v jq &>/dev/null; then
  SOURCE=$(echo "$INPUT" | jq -r '.source // "unknown"')
  FILE_PATH=$(echo "$INPUT" | jq -r '.file_path // "unknown"')
  LOG_ENTRY=$(printf '{"timestamp":"%s","source":"%s","file":"%s"}\n' "$TIMESTAMP" "$SOURCE" "$FILE_PATH")
elif command -v python3 &>/dev/null; then
  LOG_ENTRY=$(echo "$INPUT" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(json.dumps({'timestamp':'$TIMESTAMP','source':d.get('source','unknown'),'file':d.get('file_path','unknown')}))
")
else
  LOG_ENTRY="{\"timestamp\":\"$TIMESTAMP\",\"event\":\"config_change\",\"detail\":\"parser unavailable\"}"
fi

echo "$LOG_ENTRY" >> "$LOG_FILE"
exit 0
