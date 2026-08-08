param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 2147483647)]
    [int]$ProcessId,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$CandidateToken,

    [Parameter(Mandatory = $true)]
    [string]$Confirmation
)

. (Join-Path $PSScriptRoot 'ProcessTriage.Common.ps1')

$evidence = Get-WindowsProcessEvidence -ProcessId $ProcessId -SampleSeconds 1

if ($Confirmation -cne $evidence.RequiredConfirmation) {
    throw "Exact confirmation required: $($evidence.RequiredConfirmation)"
}
if ($CandidateToken -cne $evidence.CandidateToken) {
    throw 'Process identity changed after inspection; refusing to stop.'
}
if (-not $evidence.IsStopCandidate) {
    throw 'Process no longer satisfies the verified orphan stop gate.'
}

Stop-Process -Id $ProcessId -Force -ErrorAction Stop
Start-Sleep -Milliseconds 500
$alive = [bool](Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
if ($alive) {
    throw "Process $ProcessId did not stop."
}

[pscustomobject]@{
    ProcessId = $ProcessId
    Name = $evidence.Name
    Stopped = $true
    RestartGuidance = 'Restart the originating application or rerun the interrupted task if needed.'
} | ConvertTo-Json -Depth 4
