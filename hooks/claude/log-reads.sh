#!/bin/bash
# PostToolUse hook: 調査ツール呼び出し(Read/Grep/Glob/WebSearch/WebFetch)の証跡ログ
#
# なぜ .sh + python フォールバック か: このリポジトリの既存フック
# (validate-command.sh / protect-files.sh / audit-config.sh) はすべて
# 「jqがあればjq、なければpython」の同じ形をしている。JSONL整形はpython側で
# 行う(改行を含む値のエスケープや長文字列の切り詰めをjqのワンライナーで
# 安全にやるのは事故りやすい)ため、pythonの取得可否で分岐する既存パターンを
# そのまま踏襲し、整形ロジックはpythonブロックに閉じ込めた。
#
# 重要な制約(タスク仕様より):
# - 成功時は標準出力/標準エラーに何も出さない(hook出力は10,000文字上限)。
# - ファイル内容やレスポンス本文は記録しない。パス/パターン/クエリ/URLのみ。
# - サブエージェント経由の呼び出しはペイロードに agent_id/agent_type を含む
#   ことがある(メインスレッドからの呼び出しには存在しない)。存在すれば記録する。
# - これは「読んだ」ログであり「使った(採用した)」ログではない。引用の証跡
#   ではない — research_log.py 側にも明記する。
# - WebFetch の tool_response スキーマは非公開。実際に来た値をそのまま
#   捕捉するだけで、特定フィールド名がある前提は置かない。

INPUT=$(cat /dev/stdin)

if command -v python3 &>/dev/null; then
  PY=python3
elif command -v python &>/dev/null; then
  PY=python
else
  exit 0  # パーサーが使えない場合は何もせず正常終了(no-op)
fi

echo "$INPUT" | "$PY" -X utf8 "$(dirname "${BASH_SOURCE[0]}")/log-reads.py"
exit 0
