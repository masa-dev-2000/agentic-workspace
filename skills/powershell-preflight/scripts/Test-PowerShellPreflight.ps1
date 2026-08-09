[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateNotNullOrEmpty()]
    [string[]]$Path,

    [switch]$FixBom,

    [switch]$AllowNoBom,

    [switch]$AllowCStyleQuoteEscape
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

function Read-PreflightSource {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LiteralPath
    )

    $bytes = [System.IO.File]::ReadAllBytes($LiteralPath)

    if ($bytes.Length -ge 3 -and
        $bytes[0] -eq 0xEF -and
        $bytes[1] -eq 0xBB -and
        $bytes[2] -eq 0xBF) {
        $utf8Bom = New-Object System.Text.UTF8Encoding($true, $true)
        return [pscustomobject]@{
            Text           = $utf8Bom.GetString($bytes, 3, $bytes.Length - 3)
            HasUtf8Bom     = $true
            SourceEncoding = 'utf8-bom'
        }
    }

    if ($bytes.Length -ge 2 -and
        (($bytes[0] -eq 0xFF -and $bytes[1] -eq 0xFE) -or
         ($bytes[0] -eq 0xFE -and $bytes[1] -eq 0xFF))) {
        return [pscustomobject]@{
            Text           = $null
            HasUtf8Bom     = $false
            SourceEncoding = 'utf16'
        }
    }

    $strictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
    try {
        $text = $strictUtf8.GetString($bytes)
    }
    catch {
        throw "Input is neither UTF-8 nor UTF-8 with BOM: $LiteralPath"
    }

    return [pscustomobject]@{
        Text           = $text
        HasUtf8Bom     = $false
        SourceEncoding = 'utf8'
    }
}

$failureCount = 0
$knownScopes = @(
    'alias',
    'env',
    'function',
    'global',
    'local',
    'private',
    'script',
    'using',
    'variable'
)
$cStyleNeedle = [string]([char]92) + [char]34
$ambiguousColonPattern = '\$(?<name>[A-Za-z_][A-Za-z0-9_]*):'

foreach ($requestedPath in $Path) {
    $fileFailures = 0

    try {
        $resolvedPath = (Resolve-Path -LiteralPath $requestedPath -ErrorAction Stop).ProviderPath

        if ([System.IO.Path]::GetExtension($resolvedPath) -ine '.ps1') {
            throw "Expected a .ps1 file: $resolvedPath"
        }

        $source = Read-PreflightSource -LiteralPath $resolvedPath
        if ($source.SourceEncoding -eq 'utf16') {
            throw "UTF-16 is not accepted. Convert the file explicitly to UTF-8 with BOM: $resolvedPath"
        }

        if (-not $source.HasUtf8Bom) {
            if ($FixBom) {
                $utf8WithBom = New-Object System.Text.UTF8Encoding($true)
                [System.IO.File]::WriteAllText($resolvedPath, $source.Text, $utf8WithBom)
                Write-Output "NORMALIZED UTF-8 BOM: $resolvedPath"
            }
            elseif (-not $AllowNoBom) {
                [Console]::Error.WriteLine("FAIL encoding: UTF-8 BOM is required for Windows PowerShell 5.1: $resolvedPath")
                $fileFailures++
            }
        }

        $tokens = $null
        $parseErrors = $null
        [void][System.Management.Automation.Language.Parser]::ParseFile(
            $resolvedPath,
            [ref]$tokens,
            [ref]$parseErrors
        )

        foreach ($parseError in $parseErrors) {
            $extent = $parseError.Extent
            $message = "FAIL parse: {0}:{1}:{2}: {3}" -f
                $resolvedPath,
                $extent.StartLineNumber,
                $extent.StartColumnNumber,
                $parseError.Message
            [Console]::Error.WriteLine($message)
            $fileFailures++
        }

        if (-not $AllowCStyleQuoteEscape) {
            $lines = [System.IO.File]::ReadAllLines($resolvedPath)
            for ($lineIndex = 0; $lineIndex -lt $lines.Length; $lineIndex++) {
                $searchFrom = 0
                while ($searchFrom -lt $lines[$lineIndex].Length) {
                    $matchIndex = $lines[$lineIndex].IndexOf(
                        $cStyleNeedle,
                        $searchFrom,
                        [System.StringComparison]::Ordinal
                    )
                    if ($matchIndex -lt 0) {
                        break
                    }

                    $message = "FAIL quote: {0}:{1}:{2}: C-style quote escaping is ambiguous in PowerShell." -f
                        $resolvedPath,
                        ($lineIndex + 1),
                        ($matchIndex + 1)
                    [Console]::Error.WriteLine($message)
                    $fileFailures++
                    $searchFrom = $matchIndex + $cStyleNeedle.Length
                }
            }
        }

        foreach ($token in $tokens) {
            $kindName = $token.Kind.ToString()
            if ($kindName -notin @('StringExpandable', 'HereStringExpandable')) {
                continue
            }

            $matches = [System.Text.RegularExpressions.Regex]::Matches(
                $token.Extent.Text,
                $ambiguousColonPattern
            )
            foreach ($match in $matches) {
                $name = $match.Groups['name'].Value
                if ($knownScopes -contains $name.ToLowerInvariant()) {
                    continue
                }

                $hint = '$(' + $name + '): instead of $' + $name + ':'
                $message = "FAIL interpolation: {0}:{1}:{2}: Use {3}" -f
                    $resolvedPath,
                    $token.Extent.StartLineNumber,
                    $token.Extent.StartColumnNumber,
                    $hint
                [Console]::Error.WriteLine($message)
                $fileFailures++
            }
        }

        if ($fileFailures -eq 0) {
            Write-Output "PASS: $resolvedPath"
        }
    }
    catch {
        $message = "FAIL preflight: {0}: {1}" -f $requestedPath, $_.Exception.Message
        [Console]::Error.WriteLine($message)
        $fileFailures++
    }

    $failureCount += $fileFailures
}

if ($failureCount -gt 0) {
    [Console]::Error.WriteLine("PowerShell preflight failed with $failureCount issue(s).")
    exit 1
}

Write-Output 'PowerShell preflight passed.'
exit 0
