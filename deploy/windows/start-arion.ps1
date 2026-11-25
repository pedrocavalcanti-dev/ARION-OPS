param(
    [ValidateSet('web', 'worker')][string]$Role = 'web',
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [ValidateSet('production', 'development')][string]$Environment = 'production'
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'arion-common.ps1')
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$runner = Join-Path $ProjectRoot 'deploy\windows\run_process.py'
$python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$control = Enter-ArionRoleControl $ProjectRoot $Role
try {
    $existing = Get-ArionRoleStatus $ProjectRoot $Role
    if ($existing.locked) {
        $ready = Wait-ArionRoleReady $ProjectRoot $Role
        Write-Output "$Role`: running (supervisor pid $($ready.supervisor_pid), launcher pid $($ready.launcher_pid), child pid $($ready.child_pid)); instancia existente preservada."
        return
    }
    $check = Invoke-ArionPython $ProjectRoot @($runner, '--preflight', $Role, '--environment', $Environment)
    Write-Output $check.Output
    $logs = Join-Path $ProjectRoot 'var\logs'
    New-Item -ItemType Directory -Path $logs -Force | Out-Null
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $launchedAt = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    $process = Start-Process -FilePath $python -ArgumentList @(
        ('"' + $runner + '"'), $Role, '--environment', $Environment) `
        -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $logs "arion-$Role-startup.$stamp.$PID.out.log") `
        -RedirectStandardError (Join-Path $logs "arion-$Role-startup.$stamp.$PID.err.log")
    # Store launchers can exit while the effective interpreter survives.
    $ready = Wait-ArionRoleReady $ProjectRoot $Role -LaunchedAt $launchedAt
    Write-Output "$Role`: running (supervisor pid $($ready.supervisor_pid), launcher pid $($ready.launcher_pid), child pid $($ready.child_pid)); prontidao confirmada."
} finally { $control.Dispose() }
