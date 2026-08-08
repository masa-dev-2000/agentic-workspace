param(
    [ValidateRange(1, 10)]
    [int]$SampleSeconds = 2,

    [switch]$SkipSecurity
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$before = @{}
foreach ($process in (Get-Process -ErrorAction SilentlyContinue)) {
    $initialCpu = 0
    try {
        if ($null -ne $process.CPU) {
            $initialCpu = [double]$process.CPU
        }
    }
    catch {}
    $before[$process.Id] = [pscustomobject]@{
        Name = $process.ProcessName
        CPU = $initialCpu
    }
}

Start-Sleep -Seconds $SampleSeconds

$rows = @()
foreach ($process in (Get-Process -ErrorAction SilentlyContinue)) {
    $cpu = 0
    $path = ''
    try {
        if ($null -ne $process.CPU) {
            $cpu = [double]$process.CPU
        }
    }
    catch {}
    try {
        $path = [string]$process.Path
    }
    catch {}

    if (-not $before.ContainsKey($process.Id)) {
        continue
    }
    $prior = $before[$process.Id]
    $delta = [math]::Max(0, $cpu - $prior.CPU)
    $rows += [pscustomobject]@{
        Name = $process.ProcessName
        ProcessId = $process.Id
        CPUPercentOneCore = [math]::Round(($delta / $SampleSeconds) * 100, 1)
        WorkingSetMB = [math]::Round($process.WorkingSet64 / 1MB, 1)
        ExecutablePath = $path
    }
}

$aggregate = @(
    $rows |
        Group-Object Name |
        ForEach-Object {
            [pscustomobject]@{
                Name = $_.Name
                ProcessCount = $_.Count
                CPUPercentOneCore = [math]::Round(
                    ($_.Group | Measure-Object CPUPercentOneCore -Sum).Sum, 1
                )
                WorkingSetMB = [math]::Round(
                    ($_.Group | Measure-Object WorkingSetMB -Sum).Sum, 1
                )
            }
        } |
        Sort-Object CPUPercentOneCore -Descending |
        Select-Object -First 25
)

$os = Get-CimInstance Win32_OperatingSystem
$security = $null
if (-not $SkipSecurity) {
    try {
        $status = Get-MpComputerStatus -ErrorAction Stop
        $detections = @(
            Get-MpThreatDetection -ErrorAction SilentlyContinue |
                Sort-Object InitialDetectionTime -Descending |
                Select-Object -First 10 InitialDetectionTime, ThreatName, ActionSuccess, Resources
        )
        $security = [pscustomobject]@{
            Available = $true
            AntivirusEnabled = $status.AntivirusEnabled
            RealTimeProtectionEnabled = $status.RealTimeProtectionEnabled
            BehaviorMonitorEnabled = $status.BehaviorMonitorEnabled
            TamperProtected = $status.IsTamperProtected
            SignatureLastUpdated = $status.AntivirusSignatureLastUpdated
            QuickScanAgeDays = $status.QuickScanAge
            FullScanAgeDays = $status.FullScanAge
            RecentDetections = $detections
            Assessment = if (
                $status.AntivirusEnabled -and
                $status.RealTimeProtectionEnabled -and
                $detections.Count -eq 0
            ) {
                'no-warning-signs-in-these-checks'
            }
            else {
                'requires-review'
            }
        }
    }
    catch {
        $security = [pscustomobject]@{
            Available = $false
            Assessment = 'unavailable'
        }
    }
}

[pscustomobject]@{
    ObservedAt = (Get-Date).ToString('o')
    SampleSeconds = $SampleSeconds
    Memory = [pscustomobject]@{
        TotalGB = [math]::Round($os.TotalVisibleMemorySize / 1MB, 2)
        FreeGB = [math]::Round($os.FreePhysicalMemory / 1MB, 2)
    }
    TopProcesses = @(
        $rows |
            Sort-Object CPUPercentOneCore -Descending |
            Select-Object -First 25
    )
    Aggregate = $aggregate
    Security = $security
} | ConvertTo-Json -Depth 7
