---
name: windows-performance-triage
description: Diagnose and safely reduce Windows PC load while separating performance causes from malware warning signs. Use when the user says the PC is slow, heavy, hot, using too much CPU or memory, may be infected, has runaway PowerShell/Node/Codex processes, or asks to identify and stop verified orphan processes without disrupting active sessions.
---

# Windows Performance Triage

Prefer evidence over process-name guesses. Never claim that a machine is malware-free.

## Diagnose

Run the snapshot with Windows PowerShell 5.1:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/Get-WindowsLoadSnapshot.ps1
```

Use current sampled CPU, not lifetime CPU. Separate:

- performance evidence: CPU, memory, process multiplicity, command, and ancestry
- security evidence: Defender state, signature age, scan age, and detections

Describe `no warning signs found in these checks`, not `not infected`.

## Inspect a candidate

Inspect each PID before proposing a stop:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/Get-WindowsProcessEvidence.ps1 -ProcessId PID
```

Treat `IsStopCandidate` as a safety gate, not proof that stopping is desirable. Explain the exact
PID, command, parent state, sampled load, likely impact, and restart path.

## Stop only after confirmation

Ask for confirmation for every candidate. After the user confirms that PID, pass both values
returned by the evidence command:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/Stop-VerifiedWindowsProcess.ps1 `
  -ProcessId PID -CandidateToken TOKEN -Confirmation STOP-PID-PID
```

The script must refuse PID reuse, identity drift, a live parent, children, protected processes,
or a missing per-PID confirmation. Stop one process, rerun the snapshot, then reassess. Never
batch-kill Codex, Node, browser, Defender, Windows, or user processes by name.

## Reproduce only when requested

When the user explicitly asks whether orphan survival is real, run
`scripts/Test-WindowsOrphanBehavior.ps1`. It uses an isolated temporary directory, proves parent
exit and source deletion behavior, and cleans its test process in `finally`. Do not use a real PID.

Read [safety-policy.md](references/safety-policy.md) before changing candidate rules, protected
processes, confirmation behavior, or cleanup boundaries. Use `powershell-preflight` before editing
or running changed PowerShell.
