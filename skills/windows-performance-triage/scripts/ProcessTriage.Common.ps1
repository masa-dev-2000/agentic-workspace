Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

function Get-Sha256Text {
    param([Parameter(Mandatory = $true)][string]$Text)

    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
        return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
}

function Get-ProcessMap {
    $map = @{}
    foreach ($item in (Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
        $map[[int]$item.ProcessId] = $item
    }
    return $map
}

function Get-InspectorAncestorIds {
    param([Parameter(Mandatory = $true)][hashtable]$ProcessMap)

    $ids = New-Object System.Collections.Generic.HashSet[int]
    $cursor = [int]$PID
    for ($depth = 0; $depth -lt 32; $depth++) {
        if (-not $ids.Add($cursor)) {
            break
        }
        if (-not $ProcessMap.ContainsKey($cursor)) {
            break
        }
        $parent = [int]$ProcessMap[$cursor].ParentProcessId
        if ($parent -le 0) {
            break
        }
        $cursor = $parent
    }
    return @($ids)
}

function Test-ProtectedProcessName {
    param([Parameter(Mandatory = $true)][string]$Name)

    $protected = @(
        'system', 'registry', 'idle', 'smss.exe', 'csrss.exe', 'wininit.exe',
        'services.exe', 'lsass.exe', 'winlogon.exe', 'fontdrvhost.exe',
        'dwm.exe', 'explorer.exe', 'sihost.exe', 'taskhostw.exe',
        'msmpeng.exe', 'securityhealthservice.exe', 'chatgpt.exe'
    )
    return $protected -contains $Name.ToLowerInvariant()
}

function ConvertTo-ProcessCreationTime {
    param([Parameter(Mandatory = $true)]$Value)

    if ($Value -is [datetime]) {
        return [datetime]$Value
    }

    $text = [string]$Value
    if ($text -match '^\d{14}\.\d{6}[\+\-]\d{3}$') {
        return [Management.ManagementDateTimeConverter]::ToDateTime($text)
    }

    $parsed = [datetime]::MinValue
    if ([datetime]::TryParse($text, [ref]$parsed)) {
        return $parsed
    }

    throw "Unsupported process creation time: $text"
}

function Get-ProcessCpuSample {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [ValidateRange(1, 10)][int]$SampleSeconds = 2
    )

    $before = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $before) {
        return $null
    }
    $cpuBefore = $before.CPU
    Start-Sleep -Seconds $SampleSeconds
    $after = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $after) {
        return $null
    }
    return [math]::Round(([math]::Max(0, $after.CPU - $cpuBefore) / $SampleSeconds) * 100, 1)
}

function Get-WindowsProcessEvidence {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [ValidateRange(1, 10)][int]$SampleSeconds = 2
    )

    $map = Get-ProcessMap
    if (-not $map.ContainsKey($ProcessId)) {
        throw "Process $ProcessId does not exist."
    }

    $item = $map[$ProcessId]
    $parentId = [int]$item.ParentProcessId
    $parentAlive = $parentId -gt 0 -and $map.ContainsKey($parentId)
    $children = @($map.Values | Where-Object { [int]$_.ParentProcessId -eq $ProcessId })
    $ancestorIds = @(Get-InspectorAncestorIds -ProcessMap $map)
    $isInspectorAncestor = $ancestorIds -contains $ProcessId
    $name = [string]$item.Name
    $isProtected = Test-ProtectedProcessName -Name $name
    $creation = ConvertTo-ProcessCreationTime -Value $item.CreationDate
    $ageSeconds = [math]::Max(0, [int]((Get-Date) - $creation).TotalSeconds)
    $path = [string]$item.ExecutablePath
    $command = [string]$item.CommandLine
    $isManagedBackground = $command -match '(?i)[\\/]episodic-memory[\\/]dist[\\/]sync-cli\.js(?:\s|$)'
    $cpuPercent = Get-ProcessCpuSample -ProcessId $ProcessId -SampleSeconds $SampleSeconds
    $live = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $live) {
        throw "Process $ProcessId exited during inspection."
    }
    $workingSetMb = [math]::Round($live.WorkingSet64 / 1MB, 1)
    $sameNameCount = @(Get-Process -Name ([IO.Path]::GetFileNameWithoutExtension($name)) -ErrorAction SilentlyContinue).Count

    $signatureStatus = 'unavailable'
    $signer = ''
    if ($path -and (Test-Path -LiteralPath $path)) {
        try {
            $signature = Get-AuthenticodeSignature -LiteralPath $path
            $signatureStatus = [string]$signature.Status
            if ($signature.SignerCertificate) {
                $signer = [string]$signature.SignerCertificate.Subject
            }
        }
        catch {
            $signatureStatus = 'error'
        }
    }

    $loadSignal = ($cpuPercent -ne $null -and $cpuPercent -ge 50) -or
        $workingSetMb -ge 500 -or $sameNameCount -gt 10
    $candidate = (-not $parentAlive) -and $ageSeconds -ge 60 -and
        (-not $isInspectorAncestor) -and (-not $isProtected) -and
        (-not $isManagedBackground) -and $children.Count -eq 0 -and $loadSignal

    $identity = '{0}|{1}|{2}|{3}' -f $ProcessId, $creation.ToUniversalTime().Ticks, $path, $command
    $token = Get-Sha256Text -Text $identity

    return [pscustomobject]@{
        ProcessId = $ProcessId
        Name = $name
        CreationTime = $creation.ToString('o')
        AgeSeconds = $ageSeconds
        ParentId = $parentId
        ParentAlive = $parentAlive
        ChildCount = $children.Count
        ChildProcesses = @($children | Select-Object ProcessId, Name, CreationDate)
        ExecutablePath = $path
        CommandLine = $command
        SignatureStatus = $signatureStatus
        Signer = $signer
        CPUPercentOneCore = $cpuPercent
        WorkingSetMB = $workingSetMb
        SameNameProcessCount = $sameNameCount
        IsInspectorAncestor = $isInspectorAncestor
        IsProtected = $isProtected
        IsManagedBackground = $isManagedBackground
        HasLoadSignal = $loadSignal
        IsStopCandidate = $candidate
        CandidateToken = $token
        RequiredConfirmation = "STOP-PID-$ProcessId"
    }
}
