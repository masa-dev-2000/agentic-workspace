#!/bin/bash
# PreToolUse hook: 破壊的な削除操作を検出し、denyではなくaskで確認を求める
# (validate-command.sh は危険パターンをdenyでブロックするが、このフックは
#  「本当に消していいか」だけをユーザーに確認させるask専用フック)
#
# なぜ .sh + python か: 削除コマンドの判定は「安全スクラッチ除外」「複数の
# 破壊パターン」「対象パス/ブランチの抽出」を伴い、bashのgrep連鎖だけで
# 安全に書くと引用符の扱いや埋め込みが事故りやすい。log-reads.sh と同じ
# 「sh薄ラッパー + pythonで整形/判定」の形にして、jq/python判定の既存
# フォールバック方針(validate-command.sh等)は維持しつつロジックはpython
# 側に閉じ込めた。

INPUT=$(cat /dev/stdin)

if command -v python3 &>/dev/null; then
  PY=python3
elif command -v python &>/dev/null; then
  PY=python
else
  exit 0  # パーサーが使えない場合は通過させる(validate-command.shと同じ方針)
fi

echo "$INPUT" | "$PY" -X utf8 "$(dirname "${BASH_SOURCE[0]}")/guard-deletion.py"
exit 0
