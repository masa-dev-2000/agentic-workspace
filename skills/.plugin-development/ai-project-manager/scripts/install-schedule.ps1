param(
    [string]$TaskName = 'AIProjectManager-Hourly',
    [int]$IntervalHours = 1
)

$ErrorActionPreference = 'Stop'
if ($IntervalHours -lt 1 -or $IntervalHours -gt 24) {
    throw 'IntervalHours must be between 1 and 24.'
}

$node = Get-Command node.exe -ErrorAction Stop
$runner = Join-Path $PSScriptRoot 'pm-autopilot.mjs'
if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) {
    throw "Runner not found: $runner"
}

$actionArguments = '"{0}" --execute' -f $runner
$action = New-ScheduledTaskAction -Execute $node.Source -Argument $actionArguments
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Hours $IntervalHours) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

[void](Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force)
$registered = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
$registered | Select-Object TaskName, State, TaskPath
