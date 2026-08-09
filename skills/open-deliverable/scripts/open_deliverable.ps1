[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Path,

    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$allowedExtensions = @(
    ".pdf", ".pptx", ".docx", ".xlsx",
    ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".html", ".htm", ".md", ".txt", ".csv"
)

$resolved = (Resolve-Path -LiteralPath $Path).Path
$item = Get-Item -LiteralPath $resolved

if ($item.PSIsContainer) {
    throw "The target must be a file, not a directory."
}
if ($item.Length -le 0) {
    throw "The target file is empty."
}

$extension = [System.IO.Path]::GetExtension($item.Name).ToLowerInvariant()
if ($allowedExtensions -notcontains $extension) {
    throw "Unsupported deliverable extension: $extension"
}

$result = [ordered]@{
    path = $resolved
    extension = $extension
    size = $item.Length
    opened = $false
    what_if = [bool]$WhatIf
}

if (-not $WhatIf) {
    Start-Process -FilePath $resolved
    $result.opened = $true
}

$result | ConvertTo-Json -Compress
