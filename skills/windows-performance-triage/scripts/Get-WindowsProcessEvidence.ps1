param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 2147483647)]
    [int]$ProcessId,

    [ValidateRange(1, 10)]
    [int]$SampleSeconds = 2
)

. (Join-Path $PSScriptRoot 'ProcessTriage.Common.ps1')

Get-WindowsProcessEvidence -ProcessId $ProcessId -SampleSeconds $SampleSeconds |
    ConvertTo-Json -Depth 6
