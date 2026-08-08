param(
    [ValidateSet('Run', 'Parent', 'Child')]
    [string]$Mode = 'Run',

    [string]$WorkDirectory
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

if ($Mode -eq 'Child') {
    Set-Content -LiteralPath (Join-Path $WorkDirectory 'child.pid') -Value $PID -Encoding Ascii
    while ($true) {
        $value = 0.0
        for ($index = 1; $index -le 200000; $index++) {
            $value += [Math]::Sqrt($index)
        }
    }
}

if ($Mode -eq 'Parent') {
    $worker = Join-Path $WorkDirectory 'worker.ps1'
    $null = Start-Process -FilePath 'powershell.exe' -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $worker,
        '-Mode', 'Child', '-WorkDirectory', $WorkDirectory
    ) -WindowStyle Hidden -PassThru
    return
}

$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$testDirectory = Join-Path $tempRoot ('windows-orphan-test-' + [Guid]::NewGuid().ToString('N'))
$workerPath = Join-Path $testDirectory 'worker.ps1'
$childPid = $null

try {
    $null = New-Item -ItemType Directory -Path $testDirectory
    Copy-Item -LiteralPath $PSCommandPath -Destination $workerPath
    $parent = Start-Process -FilePath 'powershell.exe' -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $workerPath,
        '-Mode', 'Parent', '-WorkDirectory', $testDirectory
    ) -WindowStyle Hidden -PassThru

    $pidFile = Join-Path $testDirectory 'child.pid'
    $deadline = (Get-Date).AddSeconds(10)
    while (-not (Test-Path -LiteralPath $pidFile)) {
        if ((Get-Date) -ge $deadline) {
            throw 'Child PID was not published.'
        }
        Start-Sleep -Milliseconds 100
    }
    $childPid = [int](Get-Content -Raw -LiteralPath $pidFile)
    $null = $parent.WaitForExit(10000)
    $parentAlive = [bool](Get-Process -Id $parent.Id -ErrorAction SilentlyContinue)

    $before = Get-Process -Id $childPid -ErrorAction Stop
    $cpuBefore = $before.CPU
    Start-Sleep -Seconds 2
    $middle = Get-Process -Id $childPid -ErrorAction Stop
    $cpuMiddle = [double]$middle.CPU
    $cpuBeforeDelete = [math]::Round((($cpuMiddle - $cpuBefore) / 2) * 100, 1)

    Remove-Item -LiteralPath $workerPath -Force
    Start-Sleep -Seconds 2
    $after = Get-Process -Id $childPid -ErrorAction Stop
    $cpuAfter = [double]$after.CPU
    $cpuAfterDelete = [math]::Round((($cpuAfter - $cpuMiddle) / 2) * 100, 1)

    [pscustomobject]@{
        ParentAlive = $parentAlive
        ChildPid = $childPid
        ChildAliveBeforeSourceDelete = $true
        CPUPercentBeforeSourceDelete = $cpuBeforeDelete
        SourceExistsAfterDelete = (Test-Path -LiteralPath $workerPath)
        ChildAliveAfterSourceDelete = [bool]$after
        CPUPercentAfterSourceDelete = $cpuAfterDelete
    } | ConvertTo-Json -Depth 4
}
finally {
    if ($childPid) {
        $child = Get-CimInstance Win32_Process -Filter "ProcessId=$childPid" -ErrorAction SilentlyContinue
        if ($child -and $child.Name -eq 'powershell.exe' -and
            $child.CommandLine -match 'windows-orphan-test-') {
            Stop-Process -Id $childPid -Force -ErrorAction SilentlyContinue
        }
    }

    if (Test-Path -LiteralPath $testDirectory) {
        $resolved = [IO.Path]::GetFullPath($testDirectory)
        if ($resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -and
            ([IO.Path]::GetFileName($resolved)).StartsWith(
                'windows-orphan-test-', [StringComparison]::OrdinalIgnoreCase
            )) {
            Remove-Item -LiteralPath $resolved -Recurse -Force
        }
    }
}
