#!/bin/bash
# PreToolUse hook: Bashコマンドの危険パターンを検出してブロック
# Anthropic公式セキュリティガイドに基づくWindows向け強化版

INPUT=$(cat /dev/stdin)

# jq があれば使う、なければ Python にフォールバック
if command -v jq &>/dev/null; then
  COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command')
elif command -v python3 &>/dev/null; then
  COMMAND=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('command',''))")
elif command -v python &>/dev/null; then
  COMMAND=$(echo "$INPUT" | python -c "import sys,json; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('command',''))")
else
  exit 0  # パーサーが使えない場合は通過させる
fi

# === 破壊的ファイル操作 ===
if echo "$COMMAND" | grep -qiE 'rm\s+(-[a-z]*f|-[a-z]*r|--force|--recursive)'; then
  echo "BLOCKED: 再帰的・強制的なファイル削除は禁止されています" >&2
  exit 2
fi

# === 本番環境デプロイ: ブランチチェック ===
# wrangler deploy --env production は mainブランチからのみ許可
if echo "$COMMAND" | grep -qE 'wrangler.*(deploy|d1 execute).*(--env[[:space:]=]production|--env[[:space:]=]prod\b)'; then
  CURRENT_BRANCH=$(git branch --show-current 2>/dev/null || echo "UNKNOWN")
  if [ "$CURRENT_BRANCH" != "main" ]; then
    echo "🚨 BLOCKED: 本番デプロイ/マイグレーションはmainブランチからのみ実行可能です。" >&2
    echo "   現在のブランチ: ${CURRENT_BRANCH}" >&2
    echo "   → git checkout main && git pull origin main を実行してから再試行してください。" >&2
    exit 2
  fi
  echo "✅ ブランチ確認OK: main から本番デプロイを実行します。" >&2
fi

# === エンコード/難読化されたペイロード実行 ===
if echo "$COMMAND" | grep -qiE 'base64\s+(-d|--decode)|eval\s*\(|eval\s+"'; then
  echo "BLOCKED: エンコードされたペイロードの実行は禁止されています" >&2
  exit 2
fi

# === パイプによるリモートコード実行 ===
if echo "$COMMAND" | grep -qiE '\|\s*(ba)?sh\b|\|\s*python[23]?\b|\|\s*node\b|\|\s*perl\b'; then
  echo "BLOCKED: パイプによるコード実行は禁止されています" >&2
  exit 2
fi

# === Windows固有: PowerShellによる危険操作 ===
if echo "$COMMAND" | grep -qiE 'powershell.*(-enc|-encodedcommand|-e\s)'; then
  echo "BLOCKED: エンコードされたPowerShellコマンドは禁止されています" >&2
  exit 2
fi

if echo "$COMMAND" | grep -qiE 'Invoke-Expression|IEX\s*\(|Invoke-WebRequest|Invoke-RestMethod|Start-BitsTransfer'; then
  echo "BLOCKED: PowerShellによるリモートコード取得/実行は禁止されています" >&2
  exit 2
fi

if echo "$COMMAND" | grep -qiE 'New-Object\s+Net\.WebClient|DownloadString|DownloadFile'; then
  echo "BLOCKED: .NETによるファイルダウンロードは禁止されています" >&2
  exit 2
fi

# === Windows固有: レジストリ・システム操作 ===
if echo "$COMMAND" | grep -qiE '\breg\s+(add|delete|import|export)\b'; then
  echo "BLOCKED: レジストリの変更は禁止されています" >&2
  exit 2
fi

if echo "$COMMAND" | grep -qiE '\bschtasks\s+/(create|delete|change)\b'; then
  echo "BLOCKED: タスクスケジューラの変更は禁止されています" >&2
  exit 2
fi

if echo "$COMMAND" | grep -qiE '\bnet\s+(user|localgroup|share)\b'; then
  echo "BLOCKED: ユーザー/グループ/共有の変更は禁止されています" >&2
  exit 2
fi

# === Windows固有: certutil / bitsadmin（悪用されやすいLOLBin） ===
if echo "$COMMAND" | grep -qiE '\bcertutil\b.*(-urlcache|-decode|-encode)'; then
  echo "BLOCKED: certutilによるファイル操作は禁止されています" >&2
  exit 2
fi

if echo "$COMMAND" | grep -qiE '\bbitsadmin\b'; then
  echo "BLOCKED: bitsadminの使用は禁止されています" >&2
  exit 2
fi

# === ネットワーク: リバースシェル/データ送信パターン ===
if echo "$COMMAND" | grep -qiE '\b(nc|ncat|netcat)\s.*-[a-z]*e\b'; then
  echo "BLOCKED: netcatによるシェル転送は禁止されています" >&2
  exit 2
fi

if echo "$COMMAND" | grep -qiE '/dev/tcp/|/dev/udp/'; then
  echo "BLOCKED: bash TCP/UDPリダイレクトは禁止されています" >&2
  exit 2
fi

# === Git: 破壊的操作 ===
if echo "$COMMAND" | grep -qiE 'git\s+push\s+.*--force|git\s+push\s+-f\b'; then
  echo "BLOCKED: git force pushは禁止されています" >&2
  exit 2
fi

if echo "$COMMAND" | grep -qiE 'git\s+reset\s+--hard'; then
  echo "BLOCKED: git reset --hardは禁止されています" >&2
  exit 2
fi

# === 環境変数・シークレットの外部送信 ===
if echo "$COMMAND" | grep -qiE '(printenv|env|set)\s*\|.*(curl|wget|nc|ncat)'; then
  echo "BLOCKED: 環境変数の外部送信は禁止されています" >&2
  exit 2
fi

# === パーミッション変更 ===
if echo "$COMMAND" | grep -qiE '\bicacls\b|\btakeown\b|\bcacls\b'; then
  echo "BLOCKED: ファイルパーミッションの変更は禁止されています" >&2
  exit 2
fi

exit 0
